"""Phase 4 — critique, continuity, revision queue (initial slice)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.idempotency import (
    body_hash,
    idempotency_replay_or_conflict,
    require_idempotency_key,
    store_idempotency,
)
from director_api.api.routers.workflow_phase3 import _chapter_or_404, _scene_or_404
from director_api.api.schemas.phase4 import (
    ChapterGateWaiveBody,
    CriticReportOut,
    CriticWaiveBody,
    RevisionIssueOut,
    RevisionIssuePatch,
    SceneCritiqueBody,
)
from director_api.config import Settings
from director_api.db.models import CriticReport, Job, Project, RevisionIssue
from director_api.db.session import get_db
from director_api.services import critic_policy as critic_policy_svc
from director_api.services.audit import record_audit
from director_api.services.job_quota import assert_can_enqueue
from director_api.tasks.job_enqueue import enqueue_job_task
from director_api.tasks.worker_tasks import run_phase4_job

router = APIRouter(tags=["phase4"])


@router.post("/scenes/{scene_id}/critique")
def scene_critique(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    body: SceneCritiqueBody | None = Body(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    sc = _scene_or_404(db, settings, scene_id)
    eff = body or SceneCritiqueBody()
    ch = sc.chapter
    pj = db.get(Project, ch.project_id)
    pol = critic_policy_svc.effective_policy(pj, settings)
    if int(sc.critic_revision_count or 0) >= pol.max_revision_cycles_per_scene:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CRITIC_REVISION_CAP",
                "message": "max critic revision cycles reached for this scene",
            },
        )

    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/scenes/{scene_id}/critique"
    payload_body = eff.model_dump(mode="json", exclude_none=True)
    h = body_hash(payload_body)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "scene_critique")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="scene_critique",
        status="queued",
        payload={
            "scene_id": str(scene_id),
            "tenant_id": settings.default_tenant_id,
            "prior_report_id": str(eff.prior_report_id) if eff.prior_report_id else None,
        },
        project_id=ch.project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase4_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.post("/chapters/{chapter_id}/critique")
def chapter_critique(
    chapter_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    ch = _chapter_or_404(db, settings, chapter_id)
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/chapters/{chapter_id}/critique"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "chapter_critique")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="chapter_critique",
        status="queued",
        payload={
            "chapter_id": str(chapter_id),
            "tenant_id": settings.default_tenant_id,
        },
        project_id=ch.project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase4_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.get("/critic-reports/{report_id}")
def get_critic_report(
    report_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    r = db.get(CriticReport, report_id)
    if not r or r.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "critic report not found"})
    issues = db.scalars(
        select(RevisionIssue).where(RevisionIssue.critic_report_id == r.id).order_by(RevisionIssue.created_at)
    ).all()
    return {
        "data": {
            "report": CriticReportOut.model_validate(r).model_dump(mode="json"),
            "revision_issues": [RevisionIssueOut.model_validate(i).model_dump(mode="json") for i in issues],
        },
        "meta": meta,
    }


@router.post("/scenes/{scene_id}/critic/waive")
def waive_scene_critic(
    scene_id: UUID,
    body: CriticWaiveBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    sc = _scene_or_404(db, settings, scene_id)
    sc.critic_waived_at = datetime.now(timezone.utc)
    sc.critic_waiver_actor_id = body.actor_user_id[:256]
    sc.critic_waiver_reason = body.reason[:8000]
    record_audit(
        db,
        settings,
        action="critic.scene_waive",
        resource_type="scene",
        resource_id=scene_id,
        actor_id=body.actor_user_id,
        payload_summary=body.reason[:500],
    )
    db.commit()
    db.refresh(sc)
    return {
        "data": {
            "scene_id": str(scene_id),
            "critic_waived_at": sc.critic_waived_at.isoformat(),
            "actor_user_id": sc.critic_waiver_actor_id,
        },
        "meta": meta,
    }


@router.post("/chapters/{chapter_id}/critic-gate/waive")
def waive_chapter_critic_gate(
    chapter_id: UUID,
    body: ChapterGateWaiveBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    ch = _chapter_or_404(db, settings, chapter_id)
    ch.critic_gate_waived_at = datetime.now(timezone.utc)
    ch.critic_gate_waiver_actor_id = body.actor_user_id[:256]
    ch.critic_gate_waiver_reason = body.reason[:8000]
    ch.critic_gate_waiver_ticket_url = body.ticket_url[:2048] if body.ticket_url else None
    ch.critic_gate_status = "waived"
    record_audit(
        db,
        settings,
        action="critic.chapter_gate_waive",
        resource_type="chapter",
        resource_id=chapter_id,
        actor_id=body.actor_user_id,
        payload_summary=body.reason[:500],
    )
    db.commit()
    db.refresh(ch)
    return {
        "data": {
            "chapter_id": str(chapter_id),
            "critic_gate_status": ch.critic_gate_status,
            "critic_gate_waived_at": ch.critic_gate_waived_at.isoformat(),
        },
        "meta": meta,
    }


@router.patch("/revision-issues/{issue_id}")
def patch_revision_issue(
    issue_id: UUID,
    body: RevisionIssuePatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    issue = db.get(RevisionIssue, issue_id)
    if not issue or issue.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "revision issue not found"})
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"]:
        issue.status = str(data["status"])[:32]
    if "waiver_actor_id" in data and data["waiver_actor_id"]:
        issue.waiver_actor_id = str(data["waiver_actor_id"])[:256]
    if "waiver_reason" in data and data["waiver_reason"]:
        issue.waiver_reason = str(data["waiver_reason"])[:8000]
    if issue.status == "waived":
        issue.waiver_at = datetime.now(timezone.utc)
        if not issue.waiver_actor_id or not issue.waiver_reason:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "VALIDATION_ERROR",
                    "message": "waived status requires waiver_actor_id and waiver_reason",
                },
            )
    db.commit()
    db.refresh(issue)
    return {"data": RevisionIssueOut.model_validate(issue).model_dump(mode="json"), "meta": meta}


@router.post("/scenes/{scene_id}/critic-revision")
def scene_critic_revision(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    sc = _scene_or_404(db, settings, scene_id)
    ch = sc.chapter
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/scenes/{scene_id}/critic-revision"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "scene_critic_revision")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="scene_critic_revision",
        status="queued",
        payload={
            "scene_id": str(scene_id),
            "tenant_id": settings.default_tenant_id,
        },
        project_id=ch.project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase4_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)
