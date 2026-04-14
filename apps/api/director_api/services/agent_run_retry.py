"""Enqueue a new agent run that continues from an existing project (Telegram Retry, etc.)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.orm import Session

from director_api.db.models import AgentRun, Project

log = structlog.get_logger(__name__)

_RETRYABLE = frozenset({"failed", "cancelled", "blocked"})


def enqueue_continue_agent_run(db: Session, *, old_run_id: uuid.UUID) -> tuple[bool, str, str | None]:
    """
    Returns (ok, message, new_run_id_or_none).

    Copies ``pipeline_options_json`` from the old run, sets ``continue_from_existing`` true,
    and queues the same Celery task as Studio.
    """
    old = db.get(AgentRun, old_run_id)
    if not old:
        return False, "Run not found", None
    if old.status not in _RETRYABLE:
        return False, f"Run status {old.status!r} is not retryable from Telegram", None
    p = db.get(Project, old.project_id)
    if not p or p.tenant_id != old.tenant_id:
        return False, "Project missing", None

    po: dict[str, Any] = dict(old.pipeline_options_json or {})
    po["continue_from_existing"] = True
    if "through" not in po or not str(po.get("through") or "").strip():
        po["through"] = "full_video"

    new_id = uuid.uuid4()
    run = AgentRun(
        id=new_id,
        tenant_id=old.tenant_id,
        project_id=old.project_id,
        started_by_user_id=old.started_by_user_id,
        status="queued",
        steps_json=[],
        pipeline_options_json=po,
        pipeline_control_json={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    from director_api.tasks.worker_tasks import run_agent_run as run_agent_run_task

    run_agent_run_task.delay(str(run.id))
    log.info("agent_run_retry_enqueued", old_run_id=str(old_run_id), new_run_id=str(run.id), tenant_id=old.tenant_id)
    return True, "Queued new run with continue_from_existing", str(run.id)
