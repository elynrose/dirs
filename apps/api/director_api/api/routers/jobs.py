import hashlib
import json
import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.deps import meta_dep, settings_dep
from director_api.db.session import get_db
from director_api.api.schemas.project import JobCreate, JobOut
from director_api.config import Settings
from director_api.db.models import AgentRun, IdempotencyRecord, Job, Project
from director_api.tasks.celery_app import celery_app
from director_api.tasks.job_enqueue import enqueue_job_task
from director_api.tasks.worker_tasks import run_adapter_smoke_task

router = APIRouter(prefix="/jobs", tags=["jobs"])
log = structlog.get_logger(__name__)

ROUTE_KEY = "POST /v1/jobs"

_ALLOWLIST_STATUSES = frozenset({"queued", "running", "cancelled", "succeeded", "failed"})


@router.get("")
def list_jobs(
    project_id: UUID | None = None,
    status: str = Query(default="queued,running", description="Comma-separated statuses"),
    limit: int = Query(default=80, ge=1, le=200),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """List jobs for the tenant, optionally filtered by project and status."""
    parts = [p.strip().lower() for p in status.split(",") if p.strip()]
    st = [p for p in parts if p in _ALLOWLIST_STATUSES] if parts else ["queued", "running"]
    if not st:
        st = ["queued", "running"]
    q = select(Job).where(
        and_(
            Job.tenant_id == settings.default_tenant_id,
            Job.status.in_(tuple(st)),
        )
    )
    if project_id is not None:
        q = q.where(Job.project_id == project_id)
    rows = list(db.scalars(q.order_by(desc(Job.created_at)).limit(limit)).all())
    data = [JobOut.model_validate(j).model_dump(mode="json") for j in rows]
    return {"data": {"jobs": data, "count": len(data)}, "meta": meta}


@router.post("/clear-backlog")
def clear_task_backlog(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Cancel every **queued** Studio job and agent run for this tenant, revoke matching Celery tasks, and purge the broker queue.

    Does **not** terminate work already executing on a worker (e.g. a long ``run_agent_run`` on a solo pool). Use Stop on the agent run or cancel individual running jobs for that.
    """
    tenant = settings.default_tenant_id
    now = datetime.now(timezone.utc)

    queued_jobs = list(
        db.scalars(
            select(Job).where(
                and_(
                    Job.tenant_id == tenant,
                    Job.status == "queued",
                )
            )
        ).all()
    )
    for job in queued_jobs:
        celery_app.control.revoke(str(job.id), terminate=False)
        job.status = "cancelled"
        job.error_message = "cancelled_backlog_clear"
        job.completed_at = now

    queued_runs = list(
        db.scalars(
            select(AgentRun).where(
                and_(
                    AgentRun.tenant_id == tenant,
                    AgentRun.status == "queued",
                )
            )
        ).all()
    )
    for r in queued_runs:
        r.status = "cancelled"
        r.error_message = "Cleared from task backlog"
        r.completed_at = now
        ev = list(r.steps_json) if isinstance(r.steps_json, list) else []
        ev.append(
            {
                "step": "pipeline",
                "status": "cancelled",
                "at": now.isoformat(),
                "reason": "backlog_clear",
            }
        )
        r.steps_json = ev
        flag_modified(r, "steps_json")

    db.commit()

    purged = 0
    try:
        purged = int(celery_app.control.purge() or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("celery_purge_failed", error=str(e))

    log.info(
        "task_backlog_cleared",
        tenant_id=tenant,
        cancelled_jobs=len(queued_jobs),
        cancelled_agent_runs=len(queued_runs),
        purged_messages=purged,
    )
    return {
        "data": {
            "cancelled_jobs": len(queued_jobs),
            "cancelled_agent_runs": len(queued_runs),
            "purged_broker_messages": purged,
        },
        "meta": meta,
    }


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    job = db.get(Job, job_id)
    if not job or job.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "job not found"})
    if job.status not in ("queued", "running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_NOT_ACTIVE",
                "message": f"job is not queued or running (status={job.status})",
            },
        )
    tid = str(job_id)
    prior = job.status
    if job.status == "running":
        celery_app.control.revoke(tid, terminate=True, signal="SIGTERM")
    else:
        celery_app.control.revoke(tid, terminate=False)
    job.status = "cancelled"
    job.error_message = "cancelled_by_user"
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    log.info("job_cancelled", job_id=tid, prior_status=prior)
    return {"data": JobOut.model_validate(job).model_dump(mode="json"), "meta": meta}


@router.post("")
def create_job(
    body: JobCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key or len(idempotency_key) < 8:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": "Idempotency-Key header required (min 8 chars)"},
        )

    body_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()

    existing = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == settings.default_tenant_id,
            IdempotencyRecord.route == ROUTE_KEY,
            IdempotencyRecord.key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing:
        if existing.body_hash != body_hash:
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_CONFLICT", "message": "same key, different body"},
            )
        return JSONResponse(
            status_code=existing.response_status,
            content=existing.response_body,
        )

    prov = body.provider.lower().strip()
    if prov not in ("openai", "lm_studio", "openrouter", "fal", "gemini", "google"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "provider must be openai, lm_studio, openrouter, fal, or gemini",
            },
        )

    if body.project_id:
        p = db.get(Project, body.project_id)
        if not p or p.tenant_id != settings.default_tenant_id:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})

    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type=body.type,
        status="queued",
        payload={"provider": prov, "project_id": str(body.project_id) if body.project_id else None},
        project_id=body.project_id,
        provider=prov,
        idempotency_key=idempotency_key,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    enqueue_job_task(run_adapter_smoke_task, job.id)

    response_body = {
        "job": {
            "id": str(job.id),
            "status": job.status,
            "poll_url": f"/v1/jobs/{job.id}",
        },
        "meta": meta,
    }
    rec = IdempotencyRecord(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        route=ROUTE_KEY,
        key=idempotency_key,
        body_hash=body_hash,
        response_status=202,
        response_body=response_body,
    )
    db.add(rec)
    db.commit()

    log.info("job_enqueued", job_id=str(job.id), provider=prov, job_type=body.type)
    return JSONResponse(status_code=202, content=response_body)


@router.get("/{job_id}")
def get_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    job = db.get(Job, job_id)
    if not job or job.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "job not found"})
    return {"data": JobOut.model_validate(job).model_dump(mode="json"), "meta": meta}
