"""Recover agent runs left active after the in-process worker died (CELERY_EAGER / API restart)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.db.models import AgentRun

log = structlog.get_logger(__name__)

_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "blocked"})
_ACTIVE = frozenset({"queued", "running"})

_PROCESS_BOOT_AT = datetime.now(timezone.utc)
_WORKER_INSTANCE_ID = str(uuid.uuid4())


def process_boot_at() -> datetime:
    return _PROCESS_BOOT_AT


def current_worker_instance_id() -> str:
    return _WORKER_INSTANCE_ID


def stamp_agent_run_worker(run: AgentRun) -> None:
    """Tag ``run`` with this API process so orphan recovery can detect dead workers."""
    ctrl = dict(run.pipeline_control_json) if isinstance(run.pipeline_control_json, dict) else {}
    ctrl["worker_instance_id"] = _WORKER_INSTANCE_ID
    run.pipeline_control_json = ctrl
    flag_modified(run, "pipeline_control_json")


def _run_worker_instance_id(run: AgentRun) -> str | None:
    ctrl = run.pipeline_control_json if isinstance(run.pipeline_control_json, dict) else {}
    raw = ctrl.get("worker_instance_id")
    return str(raw).strip() if raw else None


def is_orphaned_agent_run(run: AgentRun, *, boot: datetime | None = None) -> bool:
    """True when ``run`` cannot still be executing on this API worker."""
    if run.status not in _ACTIVE:
        return False
    boot_at = boot or _PROCESS_BOOT_AT
    wid = _run_worker_instance_id(run)
    if wid and wid != _WORKER_INSTANCE_ID:
        return True
    heartbeat = run.started_at or run.created_at
    if heartbeat is not None and heartbeat < boot_at:
        return True
    return False


def _append_cancel_event(r: AgentRun, *, reason: str) -> None:
    now = datetime.now(timezone.utc)
    ev = list(r.steps_json) if r.steps_json else []
    ev.append({"step": "pipeline", "status": "cancelled", "at": now.isoformat(), "reason": reason})
    r.steps_json = ev
    flag_modified(r, "steps_json")


def force_cancel_agent_run(db: Session, r: AgentRun, *, reason: str) -> None:
    """Immediately mark an active run cancelled (no worker checkpoint required)."""
    if r.status in _TERMINAL:
        return
    now = datetime.now(timezone.utc)
    r.status = "cancelled"
    r.completed_at = now
    r.error_message = reason[:8000]
    ctrl = dict(r.pipeline_control_json) if isinstance(r.pipeline_control_json, dict) else {}
    ctrl["stop_requested"] = True
    ctrl["paused"] = False
    r.pipeline_control_json = ctrl
    flag_modified(r, "pipeline_control_json")
    _append_cancel_event(r, reason=reason)
    r.updated_at = now


def recover_orphaned_agent_runs(db: Session, *, reason: str = "worker_restarted_agent_run_orphaned") -> int:
    """Cancel queued/running agent runs that cannot still belong to this API worker."""
    boot = _PROCESS_BOOT_AT
    rows = list(db.scalars(select(AgentRun).where(AgentRun.status.in_(_ACTIVE))).all())
    victims = [r for r in rows if is_orphaned_agent_run(r, boot=boot)]
    if not victims:
        return 0
    for r in victims:
        prior = r.status
        prior_wid = _run_worker_instance_id(r)
        force_cancel_agent_run(db, r, reason=reason)
        log.warning(
            "orphaned_agent_run_recovered",
            agent_run_id=str(r.id),
            project_id=str(r.project_id),
            prior_status=prior,
            worker_instance_id=prior_wid,
        )
    db.commit()
    return len(victims)


def cancel_worker_agent_runs(db: Session, *, worker_instance_id: str, reason: str) -> int:
    """Cancel active runs owned by ``worker_instance_id`` (API graceful shutdown)."""
    rows = list(db.scalars(select(AgentRun).where(AgentRun.status.in_(_ACTIVE))).all())
    victims = [r for r in rows if _run_worker_instance_id(r) == worker_instance_id]
    if not victims:
        return 0
    for r in victims:
        force_cancel_agent_run(db, r, reason=reason)
    db.commit()
    return len(victims)


def reconcile_orphaned_active_agent_runs_for_project(
    db: Session,
    *,
    project_id: uuid.UUID,
    tenant_id: str,
    reason: str = "orphaned_agent_run_reconciled",
) -> list[AgentRun]:
    """Cancel zombie runs on ``project_id`` so a new enqueue is not blocked by 409."""
    rows = list(
        db.scalars(
            select(AgentRun).where(
                AgentRun.project_id == project_id,
                AgentRun.tenant_id == tenant_id,
                AgentRun.status.in_(_ACTIVE),
            )
        ).all()
    )
    victims = [r for r in rows if is_orphaned_agent_run(r)]
    for r in victims:
        force_cancel_agent_run(db, r, reason=reason)
    return victims


def supersede_active_agent_runs_for_project(
    db: Session,
    *,
    project_id: uuid.UUID,
    tenant_id: str,
    reason: str = "superseded_by_continue_pipeline",
) -> list[AgentRun]:
    """Cancel every active run on ``project_id`` so a new continue run can start."""
    rows = list(
        db.scalars(
            select(AgentRun).where(
                AgentRun.project_id == project_id,
                AgentRun.tenant_id == tenant_id,
                AgentRun.status.in_(_ACTIVE),
            )
        ).all()
    )
    for r in rows:
        force_cancel_agent_run(db, r, reason=reason)
    return rows
