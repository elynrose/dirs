"""Imperative Telegram operator actions (shared by commands + LLM agent)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.schemas.agent_run import AgentRunPipelineControl
from director_api.db.models import AgentRun, Asset, Chapter, Job, Project, Scene, TelegramChatStudioSession, TimelineVersion
from director_api.services.agent_run_retry import enqueue_continue_agent_run
from director_api.services.job_quota import assert_can_enqueue
from director_api.tasks.job_enqueue import enqueue_run_phase3_job, enqueue_run_phase5_job
from director_api.services.telegram_ops import (
    _active_agent_run,
    _default_context,
    _latest_agent_run,
    _resolve_project_by_token,
    format_ops_status_message,
    format_projects_list,
    get_telegram_ops,
    set_telegram_ops,
)

log = structlog.get_logger(__name__)

_ACTIVE_STATUSES = frozenset({"queued", "running", "paused"})


def _latest_timeline_version(db: Session, project_id: uuid.UUID, tenant_id: str) -> TimelineVersion | None:
    return db.scalars(
        select(TimelineVersion)
        .where(TimelineVersion.project_id == project_id, TimelineVersion.tenant_id == tenant_id)
        .order_by(desc(TimelineVersion.created_at))
        .limit(1)
    ).first()


def _ordered_scenes(db: Session, project_id: uuid.UUID, _tenant_id: str) -> list[Scene]:
    chapters = list(
        db.scalars(
            select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)
        ).all()
    )
    ch_ids = [c.id for c in chapters]
    if not ch_ids:
        return []
    scenes = list(
        db.scalars(
            select(Scene).where(Scene.chapter_id.in_(ch_ids)).order_by(Scene.chapter_id, Scene.order_index)
        ).all()
    )
    ch_order = {c.id: int(c.order_index) for c in chapters}
    scenes.sort(key=lambda s: (ch_order.get(s.chapter_id, 0), int(s.order_index)))
    return scenes


def resolve_scene_ref(db: Session, project_id: uuid.UUID, tenant_id: str, ref: str) -> Scene | None:
    t = (ref or "").strip().lower()
    if not t:
        return None
    try:
        sid = uuid.UUID(t)
        sc = db.get(Scene, sid)
        if sc:
            ch = db.get(Chapter, sc.chapter_id)
            if ch and ch.project_id == project_id:
                return sc
    except ValueError:
        pass
    if t.isdigit():
        n = int(t)
        scenes = _ordered_scenes(db, project_id, tenant_id)
        if 1 <= n <= len(scenes):
            return scenes[n - 1]
    return None


def format_scenes_list(db: Session, project_id: uuid.UUID, tenant_id: str, *, limit: int = 12) -> str:
    scenes = _ordered_scenes(db, project_id, tenant_id)
    if not scenes:
        return "No scenes yet on this project."
    lines = ["Scenes (project order):", ""]
    for i, sc in enumerate(scenes[:limit], start=1):
        label = ((sc.purpose or "").strip()[:48]) or f"Scene {i}"
        assets = list(
            db.scalars(
                select(Asset)
                .where(Asset.scene_id == sc.id, Asset.asset_type == "image")
                .order_by(desc(Asset.created_at))
                .limit(3)
            ).all()
        )
        img_bit = "no image"
        if assets:
            latest = assets[0]
            st = latest.status
            appr = "approved" if latest.approved_at else "unapproved"
            img_bit = f"image {st}/{appr}"
        lines.append(f"{i}. {label}")
        lines.append(f"   id {str(sc.id)[:8]}… · {img_bit}")
    if len(scenes) > limit:
        lines.append(f"… +{len(scenes) - limit} more")
    lines.append("")
    lines.append("Use scene number or id prefix with approve/regenerate commands.")
    return "\n".join(lines)[:4090]


def exec_select_project(
    db: Session,
    row: TelegramChatStudioSession,
    *,
    tenant_id: str,
    project_ref: str,
) -> str:
    p = _resolve_project_by_token(db, tenant_id, project_ref)
    if p is None:
        return f"No project matches “{project_ref[:40]}”. Send /projects for recent ids."
    ar = _active_agent_run(db, tenant_id, p.id) or _latest_agent_run(db, tenant_id, p.id)
    set_telegram_ops(
        row,
        {
            "active_project_id": str(p.id),
            "active_agent_run_id": str(ar.id) if ar else "",
        },
    )
    db.add(row)
    db.commit()
    msg = f"Active project: {p.title}\nId: {p.id}"
    if ar:
        msg += f"\nRun: {ar.status} ({ar.id})"
    return msg


def exec_get_status(db: Session, settings: Any, *, tenant_id: str, row: TelegramChatStudioSession) -> str:
    ops = get_telegram_ops(row)
    p, run = _default_context(db, tenant_id, ops)
    if p is None:
        return "No projects yet. Chat about your brief, then send RUN to start."
    if run and str(ops.get("active_agent_run_id") or "") != str(run.id):
        set_telegram_ops(
            row,
            {"active_project_id": str(p.id), "active_agent_run_id": str(run.id)},
        )
        db.add(row)
        db.commit()
    return format_ops_status_message(db, settings, tenant_id=tenant_id, project=p, run=run)


def exec_stop_run(db: Session, *, tenant_id: str, row: TelegramChatStudioSession) -> str:
    ops = get_telegram_ops(row)
    _p, run = _default_context(db, tenant_id, ops)
    if run is None or run.status not in _ACTIVE_STATUSES:
        return "No active pipeline run to stop."
    from director_api.api.routers.agent_runs import _apply_stop_to_agent_run

    _apply_stop_to_agent_run(db, run)
    return f"Stop requested for run {run.id}."


def exec_pause_run(db: Session, *, tenant_id: str, row: TelegramChatStudioSession) -> str:
    ops = get_telegram_ops(row)
    _p, run = _default_context(db, tenant_id, ops)
    if run is None or run.status not in _ACTIVE_STATUSES:
        return "No active run to pause."
    from director_api.api.routers.agent_runs import _handle_agent_run_control

    try:
        _handle_agent_run_control(db, run, AgentRunPipelineControl(action="pause"))
    except Exception as exc:
        return f"Could not pause: {exc!s}"
    return f"Pause requested for run {run.id} (honored at next checkpoint)."


def exec_resume_run(db: Session, *, tenant_id: str, row: TelegramChatStudioSession) -> str:
    ops = get_telegram_ops(row)
    _p, run = _default_context(db, tenant_id, ops)
    if run is None:
        return "No run on the active project."
    from director_api.api.routers.agent_runs import _handle_agent_run_control

    try:
        _handle_agent_run_control(db, run, AgentRunPipelineControl(action="resume"))
    except Exception as exc:
        return f"Could not resume: {exc!s}"
    return f"Resume signaled for run {run.id}."


def exec_retry_run(db: Session, row: TelegramChatStudioSession, *, tenant_id: str) -> str:
    ops = get_telegram_ops(row)
    _p, run = _default_context(db, tenant_id, ops)
    if run is None:
        return "No run on the active project."
    ok, msg, new_id = enqueue_continue_agent_run(db, old_run_id=run.id)
    if not ok or not new_id:
        return msg
    set_telegram_ops(row, {"active_project_id": str(run.project_id), "active_agent_run_id": new_id})
    db.add(row)
    db.commit()
    return f"Queued continue run.\nNew run id: {new_id}"


def exec_enqueue_rough_cut(
    db: Session, settings: Any, *, tenant_id: str, row: TelegramChatStudioSession
) -> str:
    ops = get_telegram_ops(row)
    p, _run = _default_context(db, tenant_id, ops)
    if p is None:
        return "No active project."
    tv = _latest_timeline_version(db, p.id, tenant_id)
    if tv is None:
        return "No timeline yet — wait for the pipeline to build one."
    try:
        assert_can_enqueue(db, settings, "rough_cut")
    except Exception as exc:
        return f"Cannot queue rough cut: {exc!s}"
    job = Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="rough_cut",
        status="queued",
        payload={
            "timeline_version_id": str(tv.id),
            "project_id": str(p.id),
            "tenant_id": tenant_id,
            "allow_unapproved_media": True,
        },
        project_id=p.id,
    )
    db.add(job)
    db.commit()
    enqueue_run_phase5_job(job.id)
    return f"Queued rough cut.\nJob: {job.id}\nTimeline: {tv.id}"


def exec_enqueue_final_cut(
    db: Session, settings: Any, *, tenant_id: str, row: TelegramChatStudioSession
) -> str:
    ops = get_telegram_ops(row)
    p, _run = _default_context(db, tenant_id, ops)
    if p is None:
        return "No active project."
    tv = _latest_timeline_version(db, p.id, tenant_id)
    if tv is None:
        return "No timeline yet."
    try:
        assert_can_enqueue(db, settings, "final_cut")
    except Exception as exc:
        return f"Cannot queue final cut: {exc!s}"
    job = Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="final_cut",
        status="queued",
        payload={
            "timeline_version_id": str(tv.id),
            "project_id": str(p.id),
            "tenant_id": tenant_id,
            "allow_unapproved_media": True,
            "burn_subtitles_into_video": False,
        },
        project_id=p.id,
    )
    db.add(job)
    db.commit()
    enqueue_run_phase5_job(job.id)
    return f"Queued final cut.\nJob: {job.id}"


def exec_approve_scene(
    db: Session, *, tenant_id: str, row: TelegramChatStudioSession, scene_ref: str
) -> str:
    ops = get_telegram_ops(row)
    p, _run = _default_context(db, tenant_id, ops)
    if p is None:
        return "No active project."
    sc = resolve_scene_ref(db, p.id, tenant_id, scene_ref)
    if sc is None:
        return f"Scene not found: {scene_ref!r}. Send /scenes to list."
    asset = db.scalars(
        select(Asset)
        .where(Asset.scene_id == sc.id, Asset.asset_type == "image", Asset.status == "succeeded")
        .order_by(desc(Asset.created_at))
        .limit(1)
    ).first()
    if asset is None:
        return f"No succeeded image to approve for scene {scene_ref}."
    asset.approved_at = datetime.now(timezone.utc)
    pj = dict(asset.params_json) if isinstance(asset.params_json, dict) else {}
    pj.pop("rejection", None)
    asset.params_json = pj
    flag_modified(asset, "params_json")
    db.commit()
    return f"Approved image {asset.id} for scene {scene_ref}."


def exec_regenerate_scene_image(
    db: Session, settings: Any, *, tenant_id: str, row: TelegramChatStudioSession, scene_ref: str
) -> str:
    ops = get_telegram_ops(row)
    p, _run = _default_context(db, tenant_id, ops)
    if p is None:
        return "No active project."
    sc = resolve_scene_ref(db, p.id, tenant_id, scene_ref)
    if sc is None:
        return f"Scene not found: {scene_ref!r}."
    try:
        assert_can_enqueue(db, settings, "scene_generate_image")
    except Exception as exc:
        return f"Cannot queue image: {exc!s}"
    job = Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="scene_generate_image",
        status="queued",
        payload={"scene_id": str(sc.id), "tenant_id": tenant_id, "generation_tier": "standard"},
        project_id=p.id,
    )
    db.add(job)
    db.commit()
    enqueue_run_phase3_job(job.id)
    return f"Queued new image for scene {scene_ref}.\nJob: {job.id}"


def execute_telegram_action(
    db: Session,
    settings: Any,
    *,
    tenant_id: str,
    row: TelegramChatStudioSession,
    action: str,
    args: dict[str, Any] | None,
) -> str | None:
    """Run a named operator action. Returns None for action 'none' (defer to setup guide)."""
    a = (action or "").strip().lower()
    params = dict(args) if isinstance(args, dict) else {}

    if a in ("", "none", "chat"):
        return None

    if a == "list_projects":
        return format_projects_list(db, tenant_id)
    if a == "get_status":
        return exec_get_status(db, settings, tenant_id=tenant_id, row=row)
    if a == "select_project":
        return exec_select_project(db, row, tenant_id=tenant_id, project_ref=str(params.get("project_ref") or ""))
    if a == "stop_run":
        return exec_stop_run(db, tenant_id=tenant_id, row=row)
    if a == "pause_run":
        return exec_pause_run(db, tenant_id=tenant_id, row=row)
    if a == "resume_run":
        return exec_resume_run(db, tenant_id=tenant_id, row=row)
    if a == "retry_run":
        return exec_retry_run(db, row, tenant_id=tenant_id)
    if a == "enqueue_rough_cut":
        return exec_enqueue_rough_cut(db, settings, tenant_id=tenant_id, row=row)
    if a == "enqueue_final_cut":
        return exec_enqueue_final_cut(db, settings, tenant_id=tenant_id, row=row)
    if a == "list_scenes":
        ops = get_telegram_ops(row)
        p, _ = _default_context(db, tenant_id, ops)
        if p is None:
            return "No active project."
        return format_scenes_list(db, p.id, tenant_id)
    if a == "approve_scene":
        return exec_approve_scene(
            db, tenant_id=tenant_id, row=row, scene_ref=str(params.get("scene_ref") or "")
        )
    if a == "regenerate_scene_image":
        return exec_regenerate_scene_image(
            db,
            settings,
            tenant_id=tenant_id,
            row=row,
            scene_ref=str(params.get("scene_ref") or ""),
        )
    return f"Unknown action: {a}"
