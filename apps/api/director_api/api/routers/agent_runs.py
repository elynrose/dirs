"""Autonomous agent runs — topic → director → research → gate → outline → scripts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.deps import meta_dep, settings_dep
from director_api.auth.deps import auth_context_dep
from director_api.auth.context import AuthContext
from director_api.api.schemas.agent_run import AgentRunCreate, AgentRunOut, AgentRunPipelineControl
from director_api.api.schemas.project import ProjectOut
from director_api.config import Settings, get_settings
from sqlalchemy import func, select
from director_api.db.models import AgentRun, Job, Project
from director_api.db.session import get_db
from director_api.tasks.job_enqueue import enqueue_agent_run
from director_api.services.agent_resume import normalize_pipeline_options_for_persist
from director_api.services.agent_run_diagnostics import (
    build_agent_run_diagnostics_text,
    user_facing_run_failure_summary,
)
from director_api.services.project_frame import coerce_clip_frame_fit
from director_api.services.agent_run_orphan_recovery import (
    reconcile_orphaned_active_agent_runs_for_project,
    supersede_active_agent_runs_for_project,
)
from director_api.services.tenant_entitlements import assert_agent_run_pipeline_allowed, assert_can_create_project
from director_api.validation.brief import validate_documentary_brief

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])
log = structlog.get_logger(__name__)

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "blocked"})
_ACTIVE_RUN_STATUSES = frozenset({"queued", "running"})


def _cascade_stop_to_project_jobs(db: Session, project_id: uuid.UUID | None) -> int:
    """Write ``payload.stop_requested=True`` into every active Job for ``project_id``.

    The agent-run-level stop flag is only visible to jobs that carry
    ``payload.agent_run_id`` (auto-pipeline scene jobs). Manual single-scene
    /generate-image / /generate-video jobs do NOT — they have no agent_run
    link. Without this cascade, the user's Stop button "stops the run" but
    in-flight manual jobs ignore it and burn another 5-15 min on the GPU.

    Returns the count of jobs that received the stop signal. Caller commits.
    """
    if project_id is None:
        return 0
    active = list(
        db.scalars(
            select(Job).where(
                Job.project_id == project_id,
                Job.status.in_(("queued", "running")),
            )
        ).all()
    )
    n = 0
    for j in active:
        ctrl_payload = dict(j.payload) if isinstance(j.payload, dict) else {}
        if ctrl_payload.get("stop_requested"):
            continue
        ctrl_payload["stop_requested"] = True
        j.payload = ctrl_payload
        flag_modified(j, "payload")
        n += 1
    return n


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
    # Cascade: project-level Stop should ALSO interrupt in-flight manual jobs
    # (which have no agent_run_id). See ``_cascade_stop_to_project_jobs``.
    cascaded = _cascade_stop_to_project_jobs(db, r.project_id)
    db.commit()
    db.refresh(r)
    log.info(
        "agent_run_stop_requested",
        agent_run_id=str(r.id),
        status=r.status,
        cascaded_jobs=cascaded,
    )
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
    tid = (tenant_id_override or "").strip()
    if not tid:
        raise ValueError("tenant_id is required for project creation")
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
        clip_frame_fit=coerce_clip_frame_fit(getattr(b, "clip_frame_fit", None)),
        no_narration=bool(getattr(b, "no_narration", False)),
        publish_to_youtube=bool(getattr(b, "publish_to_youtube", False)),
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
        if not p or p.tenant_id != auth.tenant_id:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    else:
        assert_can_create_project(db, auth.tenant_id, auth_enabled=auth_on)
        p = _project_from_brief(db, settings, body, tenant_id_override=auth.tenant_id)

    po: dict = dict(body.pipeline_options or {})
    if body.brief is not None:
        po["continue_from_existing"] = False
    elif body.project_id is not None and "continue_from_existing" not in po:
        # Existing project: default to resuming (skip completed phases) unless the client sets false explicitly.
        po["continue_from_existing"] = True
    po = normalize_pipeline_options_for_persist(po)
    if p is not None and po.get("publish_to_youtube") is not None:
        p.publish_to_youtube = bool(po.get("publish_to_youtube"))
    assert_agent_run_pipeline_allowed(
        po, db=db, tenant_id=auth.tenant_id, auth_enabled=auth_on
    )

    # Erase-consent gate: if these pipeline options will cause the worker
    # to re-run outline or replan scenes (force_pipeline_steps containing
    # "outline"/"scenes", force_replan_scenes=true, or rerun_from_step in
    # {"outline","scenes"}) AND the existing project has scenes / generated
    # assets that would be wiped, refuse the request unless
    # ``pipeline_options.confirm_erase_assets`` is true. The UI catches the
    # 409 ERASE_CONFIRMATION_REQUIRED payload (with structured scope) and
    # shows the "Erase all images or video assets to restart?" Yes/No
    # dialog before re-submitting with the flag set.
    if body.project_id is not None and p is not None:
        from director_api.services.erase_consent import (
            EraseConfirmationRequired,
            compute_outline_erase_scope,
            compute_project_replan_erase_scope,
            options_grant_erase_consent,
            pipeline_options_imply_outline_wipe,
            pipeline_options_imply_scenes_wipe,
        )

        wipe_outline = pipeline_options_imply_outline_wipe(po)
        wipe_scenes = wipe_outline or pipeline_options_imply_scenes_wipe(po)
        if (wipe_outline or wipe_scenes) and not options_grant_erase_consent(po):
            if wipe_outline:
                scope = compute_outline_erase_scope(p)
                scope_label = "outline"
            else:
                scope = compute_project_replan_erase_scope(p)
                scope_label = "scenes_replan"
            if scope.has_content_to_erase:
                err = EraseConfirmationRequired(scope_label=scope_label, scope=scope)
                raise HTTPException(status_code=409, detail=err.to_dict())

    if body.project_id is not None and po.get("continue_from_existing"):
        superseded = supersede_active_agent_runs_for_project(
            db,
            project_id=p.id,
            tenant_id=auth.tenant_id,
        )
        if superseded:
            for r in superseded:
                _cascade_stop_to_project_jobs(db, r.project_id)
            db.commit()
            log.info(
                "agent_runs_superseded_for_continue",
                project_id=str(p.id),
                count=len(superseded),
                run_ids=[str(r.id) for r in superseded[:8]],
            )
    elif body.project_id is not None:
        reconciled = reconcile_orphaned_active_agent_runs_for_project(
            db,
            project_id=p.id,
            tenant_id=auth.tenant_id,
        )
        if reconciled:
            for r in reconciled:
                _cascade_stop_to_project_jobs(db, r.project_id)
            db.commit()
            log.info(
                "agent_runs_orphans_reconciled",
                project_id=str(p.id),
                count=len(reconciled),
                run_ids=[str(r.id) for r in reconciled[:8]],
            )

    if body.project_id is not None:
        active_n = db.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.project_id == p.id,
                AgentRun.tenant_id == auth.tenant_id,
                AgentRun.status.in_(_ACTIVE_RUN_STATUSES),
            )
        )
        if int(active_n or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AGENT_RUN_ALREADY_ACTIVE",
                    "message": "This project already has a queued or running agent run — stop it before starting another.",
                },
            )

    starter_uid = int(auth.user_id) if auth.user_id else None
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=auth.tenant_id,
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
    enqueue_agent_run(run.id)
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
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    return {"data": AgentRunOut.model_validate(r).model_dump(mode="json"), "meta": meta}


@router.get("/{agent_run_id}/events")
def get_agent_run_events(
    agent_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    events = r.steps_json if isinstance(r.steps_json, list) else []
    return {"data": {"events": events}, "meta": meta}


@router.get("/{agent_run_id}/diagnostics")
def get_agent_run_diagnostics(
    agent_run_id: uuid.UUID,
    format: str = Query(default="json", alias="format", pattern="^(json|text)$"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
):
    """
    Technical log for a run — full error text, pipeline options, media stats, step outcomes.
    Use ``?format=text`` to download a plain-text file (linked from Studio failure UI).
    """
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    body = build_agent_run_diagnostics_text(db, r, settings)
    if format == "text":
        filename = f"directely-run-{agent_run_id}.log"
        return Response(
            content=body,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    summary = user_facing_run_failure_summary(r.error_message) if r.error_message else None
    return {
        "data": {
            "agent_run_id": str(agent_run_id),
            "status": r.status,
            "summary": summary,
            "technical_log": body,
            "download_url": f"/v1/agent-runs/{agent_run_id}/diagnostics?format=text",
        },
        "meta": meta,
    }


@router.post("/{agent_run_id}/control")
def post_agent_run_control(
    agent_run_id: uuid.UUID,
    body: AgentRunPipelineControl,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Pause, resume, or stop the autonomous pipeline (worker honors flags at step boundaries)."""
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != auth.tenant_id:
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
    auth: AuthContext = Depends(auth_context_dep),
) -> Response:
    """Remove a finished agent run row (terminal status only)."""
    r = db.get(AgentRun, agent_run_id)
    if not r or r.tenant_id != auth.tenant_id:
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
