"""Autonomous agent runs — topic → director → research → gate → outline → scripts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.deps import meta_dep, settings_dep
from director_api.auth.deps import auth_context_dep
from director_api.auth.context import AuthContext
from director_api.api.schemas.agent_run import AgentRunCreate, AgentRunOut, AgentRunPipelineControl
from director_api.api.schemas.project import ProjectOut
from director_api.config import Settings, get_settings
from director_api.db.models import AgentRun, Project
from director_api.db.session import get_db
from director_api.tasks.worker_tasks import run_agent_run
from director_api.services.tenant_entitlements import assert_agent_run_pipeline_allowed, assert_can_create_project
from director_api.validation.brief import validate_documentary_brief

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])
log = structlog.get_logger(__name__)

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "blocked"})


def _apply_stop_to_agent_run(db: Session, r: AgentRun) -> AgentRun:
    """Set stop flag (and cancel immediately if still queued). Caller must load `r` for this tenant."""
    if r.status in _TERMINAL_STATUSES:
        return r
    ctrl = dict(r.pipeline_control_json) if isinstance(r.pipeline_control_json, dict) else {}
    ctrl["stop_requested"] = True
    ctrl["paused"] = False
    r.pipeline_control_json = ctrl
    flag_modified(r, "pipeline_control_json")
    if r.status == "queued":
        r.status = "cancelled"
        r.error_message = "Stopped by user"
        r.completed_at = datetime.now(timezone.utc)
        ev = list(r.steps_json) if r.steps_json else []
        ev.append(
            {
                "step": "pipeline",
                "status": "cancelled",
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": "user_stop_while_queued",
            }
        )
        r.steps_json = ev
        flag_modified(r, "steps_json")
    # Bumps row version so clients polling ``updated_at`` / SSE see the stop signal immediately
    # (status may stay ``running`` until the worker hits a checkpoint).
    r.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(r)
    log.info("agent_run_stop_requested", agent_run_id=str(r.id), status=r.status)
    return r


def _handle_agent_run_control(db: Session, r: AgentRun, body: AgentRunPipelineControl) -> AgentRun:
    """Pause, resume, or stop. Commits."""
    if body.action == "stop":
        return _apply_stop_to_agent_run(db, r)

    if r.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"code": "AGENT_RUN_NOT_ACTIVE", "message": "run is not active (cannot pause/resume)"},
        )

    ctrl = dict(r.pipeline_control_json) if isinstance(r.pipeline_control_json, dict) else {}

    if body.action == "pause":
        if r.status not in ("running", "paused", "queued"):
            raise HTTPException(
                status_code=409,
                detail={"code": "AGENT_RUN_CANNOT_PAUSE", "message": f"cannot pause from status {r.status!r}"},
            )
        ctrl["paused"] = True
        r.pipeline_control_json = ctrl
        flag_modified(r, "pipeline_control_json")
        db.commit()
        db.refresh(r)
        return r

    # resume
    ctrl["paused"] = False
    r.pipeline_control_json = ctrl
    flag_modified(r, "pipeline_control_json")
    if r.status == "paused":
        r.status = "running"
    db.commit()
    db.refresh(r)
    return r


def _project_from_brief(
    db: Session,
    settings: Settings,
    body: AgentRunCreate,
    *,
    tenant_id_override: str | None = None,
) -> Project:
    assert body.brief is not None
    b = body.brief
    try:
        validate_documentary_brief(b.brief_dict())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(e)},
        ) from e
    tid = (tenant_id_override or "").strip() or settings.default_tenant_id
    p = Project(
        tenant_id=tid,
        title=b.title,
        topic=b.topic,
        status="draft",
        research_min_sources=b.research_min_sources if b.research_min_sources is not None else 3,
        target_runtime_minutes=b.target_runtime_minutes,
        audience=b.audience,
        tone=b.tone,
        visual_style=b.visual_style,
        narration_style=b.narration_style,
        factual_strictness=b.factual_strictness,
        music_preference=b.music_preference,
        budget_limit=b.budget_limit,
        preferred_text_provider=b.preferred_text_provider,
        preferred_image_provider=b.preferred_image_provider,
        preferred_video_provider=b.preferred_video_provider,
        preferred_speech_provider=b.preferred_speech_provider,
        frame_aspect_ratio=(b.frame_aspect_ratio or "16:9"),
    )
    db.add(p)
    db.flush()
    return p


@router.post("")
def create_agent_run(
    body: AgentRunCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
):
    auth_on = bool(get_settings().director_auth_enabled)
    if body.project_id is not None:
        p = db.get(Project, body.project_id)
        if not p or p.tenant_id != settings.default_tenant_id:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    else:
        assert_can_create_project(db, settings.default_tenant_id, auth_enabled=auth_on)
        p = _project_from_brief(db, settings, body)

    po: dict = dict(body.pipeline_options or {})
    if body.brief is not None:
        po["continue_from_existing"] = False
    elif body.project_id is not None and "continue_from_existing" not in po:
        # Existing project: default to resuming (skip completed phases) unless the client sets false explicitly.
        po["continue_from_existing"] = True
    assert_agent_run_pipeline_allowed(
        po, db=db, tenant_id=settings.default_tenant_id, auth_enabled=auth_on
    )
    starter_uid = int(auth.user_id) if auth.user_id else None
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        project_id=p.id,
        started_by_user_id=starter_uid,
        status="queued",
        steps_json=[],
        pipeline_options_json=po,
        pipeline_control_json={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_agent_run.delay(str(run.id))
    log.info("agent_run_enqueued", agent_run_id=str(run.id), project_id=str(p.id))
    response_body = {
        "data": {
            "agent_run": AgentRunOut.model_validate(run).model_dump(mode="json"),
            "project": ProjectOut.model_validate(p).model_dump(mode="json"),
            "poll_url": f"/v1/agent-runs/{run.id}",
        },
        "meta": meta,
    }
    return JSONResponse(status_code=202, content=response_body)


@router.get("/{agent_run_id}")
def get_agent_run(
    agent_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    return {"data": AgentRunOut.model_validate(r).model_dump(mode="json"), "meta": meta}


@router.get("/{agent_run_id}/events")
def get_agent_run_events(
    agent_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    events = r.steps_json if isinstance(r.steps_json, list) else []
    return {"data": {"events": events}, "meta": meta}


@router.post("/{agent_run_id}/control")
def post_agent_run_control(
    agent_run_id: uuid.UUID,
    body: AgentRunPipelineControl,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Pause, resume, or stop the autonomous pipeline (worker honors flags at step boundaries)."""
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})

    if body.action == "stop" and r.status in _TERMINAL_STATUSES:
        return {"data": AgentRunOut.model_validate(r).model_dump(mode="json"), "meta": meta}

    out = _handle_agent_run_control(db, r, body)
    return {"data": AgentRunOut.model_validate(out).model_dump(mode="json"), "meta": meta}


@router.delete("/{agent_run_id}")
def delete_agent_run(
    agent_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> Response:
    """Remove a finished agent run row (terminal status only)."""
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    if r.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AGENT_RUN_ACTIVE",
                "message": "cannot delete an active run — stop it first, then delete",
            },
        )
    db.delete(r)
    db.commit()
    log.info("agent_run_deleted", agent_run_id=str(agent_run_id))
    return Response(status_code=204)
