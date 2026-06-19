"""Agent-run pause/stop checkpoint and pipeline event helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from director_api.db.models import AgentRun
from director_api.tasks.agent_exceptions import AgentRunPausedYield


def append_event(run: AgentRun, step: str, status: str, **extra: Any) -> None:
    events = list(run.steps_json) if run.steps_json else []
    row: dict[str, Any] = {
        "step": step,
        "status": status,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    for k, v in extra.items():
        if v is not None:
            row[k] = v
    events.append(row)
    run.steps_json = events
    flag_modified(run, "steps_json")
    run.updated_at = datetime.now(timezone.utc)


def touch_agent_run_progress(
    db: Any,
    agent_run_uuid: uuid.UUID,
    step: str,
    *,
    status: str = "progress",
    **extra: Any,
) -> None:
    """Lightweight heartbeat during long inline steps (coverage, ComfyUI batches)."""
    run = db.get(AgentRun, agent_run_uuid)
    if run is None or run.status not in ("running", "paused", "queued"):
        return
    append_event(run, step, status, **extra)
    db.commit()


def pipeline_control_dict(raw: Any) -> dict[str, bool]:
    if not isinstance(raw, dict):
        return {"paused": False, "stop_requested": False}
    return {
        "paused": bool(raw.get("paused")),
        "stop_requested": bool(raw.get("stop_requested")),
    }


def payload_agent_run_uuid(payload: dict[str, Any]) -> uuid.UUID | None:
    v = payload.get("agent_run_id")
    if v is None:
        return None
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def _merge_pipeline_control(run: AgentRun, **updates: bool) -> None:
    cur = dict(run.pipeline_control_json) if isinstance(run.pipeline_control_json, dict) else {}
    for k, v in updates.items():
        cur[k] = bool(v)
    run.pipeline_control_json = cur
    flag_modified(run, "pipeline_control_json")


def agent_run_checkpoint(db: Any, agent_run_uuid: uuid.UUID) -> str:
    """Honor pause/stop from API. Returns ``ok`` or ``stop``; raises ``AgentRunPausedYield`` when paused."""
    db.expire_all()
    r = db.get(AgentRun, agent_run_uuid)
    if not r:
        return "stop"
    ctrl = pipeline_control_dict(r.pipeline_control_json)
    if ctrl["stop_requested"]:
        if r.status not in ("cancelled", "failed", "succeeded", "blocked"):
            r.status = "cancelled"
            r.error_message = "Stopped by user"
            r.completed_at = datetime.now(timezone.utc)
            r.current_step = None
            _merge_pipeline_control(r, paused=False)
            append_event(r, "pipeline", "cancelled", reason="user_stop")
            db.commit()
        return "stop"
    if ctrl["paused"]:
        if r.status == "running":
            r.status = "paused"
            append_event(r, "pipeline", "paused")
            db.commit()
        raise AgentRunPausedYield()
    if r.status == "paused":
        r.status = "running"
        cur = dict(r.pipeline_control_json) if isinstance(r.pipeline_control_json, dict) else {}
        cur["paused"] = False
        r.pipeline_control_json = cur
        flag_modified(r, "pipeline_control_json")
        append_event(r, "pipeline", "resumed")
        db.commit()
    return "ok"
