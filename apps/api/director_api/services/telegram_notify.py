"""Post-run and run-started notifications to Telegram (best-effort)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from director_api.config import get_settings
from director_api.db.models import AgentRun, Project, TimelineVersion
from director_api.db.session import SessionLocal
from director_api.services.agent_run_failure_copy import summarize_agent_run_failure
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.services.telegram_client import telegram_send_document, telegram_send_message
from ffmpeg_pipelines.paths import path_is_readable_file

log = structlog.get_logger(__name__)

_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "blocked"})
_NOTIFY_FAIL = frozenset({"failed", "cancelled", "blocked"})

_STEP_LABELS: dict[str, str] = {
    "director": "Story direction",
    "research": "Research",
    "outline": "Chapter outline",
    "chapters": "Chapter scripts",
    "scenes": "Scene planning",
    "story_research_review": "Fact check",
    "auto_characters": "Character bible",
    "auto_narration": "Voice-over (TTS)",
    "auto_images": "Scene images",
    "auto_videos": "Scene videos",
    "auto_timeline": "Build timeline",
    "auto_rough_cut": "Rough cut",
    "auto_final_cut": "Final video",
    "auto_scene_coverage": "Extra media pass",
}


def _step_label(step: str) -> str:
    key = (step or "").strip()
    return _STEP_LABELS.get(key, key.replace("_", " ").title() or "Pipeline step")


def _studio_open_url(settings, agent_run_id: str) -> str | None:
    base = (getattr(settings, "director_public_app_url", None) or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/?agentRun={agent_run_id}"


def _failure_reply_markup(settings, agent_run_id: str) -> dict[str, Any] | None:
    rows: list[list[dict[str, str]]] = []
    url = _studio_open_url(settings, agent_run_id)
    retry_btn = {"text": "Retry pipeline", "callback_data": f"retry_ar:{agent_run_id}"}
    if url:
        rows.append([{"text": "Open Studio", "url": url}, retry_btn])
    else:
        rows.append([retry_btn])
    return {"inline_keyboard": rows}


def _telegram_credentials(settings) -> tuple[str, str] | None:
    token = (getattr(settings, "telegram_bot_token", None) or "").strip()
    chat = (getattr(settings, "telegram_chat_id", None) or "").strip()
    if not token or not chat:
        return None
    return token, chat


def telegram_notify_phase_complete(
    settings,
    *,
    project_title: str,
    agent_run_id: str,
    step: str,
    **extra: Any,
) -> None:
    """One message when a named pipeline phase completes (best-effort)."""
    if not bool(getattr(settings, "telegram_notify_phase_completions", True)):
        return
    creds = _telegram_credentials(settings)
    if not creds:
        return
    token, chat = creds
    title = (project_title or "Project").strip() or "Project"
    label = _step_label(step)
    lines = [f"Directely — {label} done", f"Project: {title}", f"Run: {agent_run_id}"]
    if extra.get("generated") is not None:
        lines.append(f"Generated: {extra['generated']}")
    if extra.get("skipped_existing") is not None:
        lines.append(f"Skipped (already had media): {extra['skipped_existing']}")
    if extra.get("timeline_version_id"):
        lines.append(f"Timeline: {extra['timeline_version_id']}")
    if extra.get("failure_reason_summary"):
        lines.append(str(extra["failure_reason_summary"])[:500])
    if extra.get("summary") and step == "auto_timeline":
        lines.append(str(extra["summary"])[:500])
    try:
        telegram_send_message(token, chat, "\n".join(lines))
    except Exception as exc:
        log.warning(
            "telegram_phase_complete_failed",
            step=step,
            agent_run_id=agent_run_id,
            error=str(exc)[:500],
        )


def _mark_terminal_telegram_sent(db, run: AgentRun) -> None:
    ctrl = dict(run.pipeline_control_json) if isinstance(run.pipeline_control_json, dict) else {}
    if ctrl.get("telegram_terminal_notified"):
        return
    ctrl["telegram_terminal_notified"] = True
    run.pipeline_control_json = ctrl
    flag_modified(run, "pipeline_control_json")
    db.commit()


def _terminal_already_notified(run: AgentRun) -> bool:
    ctrl = run.pipeline_control_json if isinstance(run.pipeline_control_json, dict) else {}
    return bool(ctrl.get("telegram_terminal_notified"))


# Legacy guard: skip very stale duplicate worker passes (replaced by telegram_terminal_notified flag).
_MAX_NOTIFY_AGE_SEC = 1800.0


def _final_cut_path_for_project(
    db,
    *,
    storage_root: str | Path,
    project_id: uuid.UUID,
    tenant_id: str,
) -> Path | None:
    row = db.scalars(
        select(TimelineVersion)
        .where(TimelineVersion.project_id == project_id, TimelineVersion.tenant_id == tenant_id)
        .order_by(TimelineVersion.created_at.desc())
        .limit(1)
    ).first()
    if not row:
        return None
    root = Path(storage_root).resolve()
    p = root / "exports" / str(project_id) / str(row.id) / "final_cut.mp4"
    if path_is_readable_file(p):
        return p
    return None


def telegram_notify_run_started(settings, project_title: str, agent_run_id: str) -> None:
    token = (getattr(settings, "telegram_bot_token", None) or "").strip()
    chat = (getattr(settings, "telegram_chat_id", None) or "").strip()
    if not token or not chat:
        return
    title = (project_title or "Project").strip() or "Project"
    text = f"Directely pipeline started.\nProject: {title}\nRun: {agent_run_id}"
    try:
        telegram_send_message(token, chat, text)
    except Exception as exc:
        log.warning("telegram_run_started_failed", agent_run_id=agent_run_id, error=str(exc))


def telegram_notify_after_agent_run(agent_run_id: str) -> None:
    try:
        aid = uuid.UUID(agent_run_id)
    except ValueError:
        return
    with SessionLocal() as db:
        run = db.get(AgentRun, aid)
        if not run or run.status not in _TERMINAL:
            return
        base = get_settings()
        settings = resolve_runtime_settings(db, base, run.tenant_id, user_id=run.started_by_user_id)
        if _terminal_already_notified(run):
            return
        creds = _telegram_credentials(settings)
        if not creds:
            return
        token, chat = creds
        completed = run.completed_at
        if completed is None:
            return
        age = (datetime.now(timezone.utc) - completed).total_seconds()
        if age > _MAX_NOTIFY_AGE_SEC:
            return
        project = db.get(Project, run.project_id)
        title = (project.title if project else "Project").strip() or "Project"
        status = run.status
        err = (run.error_message or "").strip()
        if status == "succeeded":
            msg = f"Pipeline finished: succeeded.\nProject: {title}\nRun: {agent_run_id}"
            try:
                telegram_send_message(token, chat, msg)
                _mark_terminal_telegram_sent(db, run)
            except Exception as exc:
                log.warning("telegram_terminal_message_failed", agent_run_id=agent_run_id, error=str(exc))
            if bool(getattr(settings, "youtube_share_watch_link_in_telegram", False)):
                row_tv = db.scalars(
                    select(TimelineVersion)
                    .where(TimelineVersion.project_id == run.project_id, TimelineVersion.tenant_id == run.tenant_id)
                    .order_by(TimelineVersion.created_at.desc())
                    .limit(1)
                ).first()
                yu: dict[str, Any] | None = None
                if row_tv and isinstance(row_tv.timeline_json, dict):
                    raw = row_tv.timeline_json.get("youtube_last_upload")
                    yu = raw if isinstance(raw, dict) else None
                wurl = str((yu or {}).get("watch_url") or "").strip()
                if wurl:
                    try:
                        telegram_send_message(token, chat, f"YouTube: {wurl}")
                    except Exception as exc:
                        log.warning("telegram_youtube_link_failed", agent_run_id=agent_run_id, error=str(exc))
            vid = _final_cut_path_for_project(
                db,
                storage_root=settings.local_storage_root,
                project_id=run.project_id,
                tenant_id=run.tenant_id,
            )
            if vid:
                try:
                    telegram_send_document(
                        token,
                        chat,
                        vid,
                        caption=f"{title} — final_cut.mp4",
                    )
                except Exception as exc:
                    log.warning(
                        "telegram_send_final_video_failed",
                        agent_run_id=agent_run_id,
                        error=str(exc),
                    )
            return
        if not bool(getattr(settings, "telegram_notify_pipeline_failures", False)):
            return
        if status == "failed":
            summary = summarize_agent_run_failure(err)
            body = f"Pipeline finished: failed.\nProject: {title}\nRun: {agent_run_id}\n\n{summary}"
        elif status == "cancelled":
            body = f"Pipeline cancelled.\nProject: {title}\nRun: {agent_run_id}"
        else:
            body = f"Pipeline blocked or stopped.\nProject: {title}\nRun: {agent_run_id}\nStatus: {status}"
            if err:
                body += f"\n\n{summarize_agent_run_failure(err)}"
        markup = _failure_reply_markup(settings, agent_run_id) if status in _NOTIFY_FAIL else None
        try:
            telegram_send_message(token, chat, body, reply_markup=markup)
            _mark_terminal_telegram_sent(db, run)
        except Exception as exc:
            log.warning("telegram_terminal_message_failed", agent_run_id=agent_run_id, error=str(exc))
