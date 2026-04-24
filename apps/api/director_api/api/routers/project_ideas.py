"""Project ideas: LLM generation, save, run pipeline, schedule."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.schemas.agent_run import AgentRunOut
from director_api.api.schemas.project import ProjectOut
from director_api.api.schemas.project_ideas import (
    IdeaGenerateIn,
    IdeaGenerateOut,
    IdeaInstantRunIn,
    IdeaItem,
    IdeaRunIn,
    IdeaScheduleIn,
    IdeaScheduledRunOut,
    ProjectIdeaCreate,
    ProjectIdeaOut,
)
from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.config import Settings, get_settings
from director_api.db.models import IdeaScheduledRun, ProjectIdea
from director_api.db.session import get_db
from director_api.services.project_ideas import (
    cancel_pending_schedules_for_idea,
    create_project_and_start_agent_run,
    generate_idea_items,
    list_ideas_for_tenant,
)

router = APIRouter(prefix="/ideas", tags=["ideas"])
log = structlog.get_logger(__name__)


def _tid(settings: Settings) -> str:
    return settings.default_tenant_id


@router.post("/generate")
def generate_ideas(
    body: IdeaGenerateIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    items, err = generate_idea_items(settings, body.topic)
    if err:
        raise HTTPException(
            status_code=502,
            detail={"code": "IDEAS_GENERATE_FAILED", "message": err},
        )
    return {
        "data": IdeaGenerateOut(ideas=[IdeaItem(title=i.title, description=i.description) for i in items]).model_dump(
            mode="json"
        ),
        "meta": meta,
    }


@router.get("")
def list_ideas(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    rows = list_ideas_for_tenant(db, _tid(settings))
    return {
        "data": [ProjectIdeaOut.model_validate(r).model_dump(mode="json") for r in rows],
        "meta": meta,
    }


@router.post("")
def save_idea(
    body: ProjectIdeaCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    auth_on = bool(get_settings().director_auth_enabled)
    from director_api.services.tenant_entitlements import assert_can_create_project

    assert_can_create_project(db, _tid(settings), auth_enabled=auth_on)
    row = ProjectIdea(
        id=uuid.uuid4(),
        tenant_id=_tid(settings),
        source_topic=body.source_topic.strip(),
        title=body.title.strip(),
        description=body.description.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"data": ProjectIdeaOut.model_validate(row).model_dump(mode="json"), "meta": meta}


@router.post("/run-instant")
def run_idea_instant(
    body: IdeaInstantRunIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Create project + agent run from ad-hoc title/description (no saved idea row)."""
    auth_on = bool(get_settings().director_auth_enabled)
    uid = int(auth.user_id) if auth.user_id else None
    try:
        p, run = create_project_and_start_agent_run(
            db,
            settings=settings,
            tenant_id=_tid(settings),
            title=body.title.strip(),
            topic=body.description.strip(),
            target_runtime_minutes=body.target_runtime_minutes,
            started_by_user_id=uid,
            auth_enabled=auth_on,
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("idea_instant_run_failed", error=str(e)[:500])
        raise HTTPException(
            status_code=400,
            detail={"code": "IDEA_RUN_FAILED", "message": str(e)[:2000]},
        ) from e
    return JSONResponse(
        status_code=202,
        content={
            "data": {
                "agent_run": AgentRunOut.model_validate(run).model_dump(mode="json"),
                "project": ProjectOut.model_validate(p).model_dump(mode="json"),
                "poll_url": f"/v1/agent-runs/{run.id}",
            },
            "meta": meta,
        },
    )


@router.post("/{idea_id}/run")
def run_idea(
    idea_id: uuid.UUID,
    body: IdeaRunIn | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    idea = db.get(ProjectIdea, idea_id)
    if not idea or idea.tenant_id != _tid(settings):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "idea not found"})
    auth_on = bool(get_settings().director_auth_enabled)
    rt = body.target_runtime_minutes if body else 10
    uid = int(auth.user_id) if auth.user_id else None
    try:
        p, run = create_project_and_start_agent_run(
            db,
            settings=settings,
            tenant_id=_tid(settings),
            title=idea.title,
            topic=idea.description,
            target_runtime_minutes=rt,
            started_by_user_id=uid,
            auth_enabled=auth_on,
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("idea_run_failed", idea_id=str(idea_id), error=str(e)[:500])
        raise HTTPException(
            status_code=400,
            detail={"code": "IDEA_RUN_FAILED", "message": str(e)[:2000]},
        ) from e
    return JSONResponse(
        status_code=202,
        content={
            "data": {
                "agent_run": AgentRunOut.model_validate(run).model_dump(mode="json"),
                "project": ProjectOut.model_validate(p).model_dump(mode="json"),
                "poll_url": f"/v1/agent-runs/{run.id}",
            },
            "meta": meta,
        },
    )


@router.post("/{idea_id}/schedule")
def schedule_idea(
    idea_id: uuid.UUID,
    body: IdeaScheduleIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    idea = db.get(ProjectIdea, idea_id)
    if not idea or idea.tenant_id != _tid(settings):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "idea not found"})
    when = body.scheduled_at
    if when.tzinfo is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "SCHEDULE_TIMEZONE_REQUIRED", "message": "scheduled_at must include a timezone offset"},
        )
    when_utc = when.astimezone(timezone.utc)
    if when_utc <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=422,
            detail={"code": "SCHEDULE_PAST", "message": "scheduled_at must be in the future"},
        )
    cancel_pending_schedules_for_idea(db, _tid(settings), idea_id)
    uid = int(auth.user_id) if auth.user_id else None
    row = IdeaScheduledRun(
        id=uuid.uuid4(),
        tenant_id=_tid(settings),
        idea_id=idea.id,
        scheduled_at=when,
        status="pending",
        created_by_user_id=uid,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"data": IdeaScheduledRunOut.model_validate(row).model_dump(mode="json"), "meta": meta}


@router.get("/schedules/list")
def list_schedules(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    status: str | None = None,
) -> dict:
    q = select(IdeaScheduledRun).where(IdeaScheduledRun.tenant_id == _tid(settings))
    if status:
        q = q.where(IdeaScheduledRun.status == status.strip())
    rows = list(db.scalars(q.order_by(desc(IdeaScheduledRun.scheduled_at)).limit(100)).all())
    return {
        "data": [IdeaScheduledRunOut.model_validate(r).model_dump(mode="json") for r in rows],
        "meta": meta,
    }


@router.delete("/schedules/{schedule_id}")
def cancel_schedule(
    schedule_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    row = db.get(IdeaScheduledRun, schedule_id)
    if not row or row.tenant_id != _tid(settings):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "schedule not found"})
    if row.status != "pending":
        raise HTTPException(
            status_code=409,
            detail={"code": "SCHEDULE_NOT_CANCELLABLE", "message": "only pending schedules can be cancelled"},
        )
    row.status = "cancelled"
    db.commit()
    return {"data": {"cancelled": True}, "meta": meta}


@router.delete("/{idea_id}")
def delete_idea(
    idea_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    row = db.get(ProjectIdea, idea_id)
    if not row or row.tenant_id != _tid(settings):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "idea not found"})
    db.delete(row)
    db.commit()
    return {"data": {"deleted": True}, "meta": meta}
