"""Celery worker liveness helpers for the Studio UI.

With ``--pool=solo`` (Windows default), the worker runs tasks in the same process
and often cannot answer ``control.ping`` until the current task yields or finishes.
Users saw the Celery indicator flip to *offline* right after opening a project even
though the worker was still running — the UI had just started polling / SSE checks.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import AgentRun, Job

log = structlog.get_logger(__name__)


def tenant_has_running_async_work(db: Session, tenant_id: str) -> bool:
    """True if this tenant has an agent run or Studio job marked *running* in the DB."""
    rid = db.scalar(
        select(AgentRun.id).where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.status == "running",
        ).limit(1)
    )
    if rid is not None:
        return True
    jid = db.scalar(
        select(Job.id).where(
            Job.tenant_id == tenant_id,
            Job.status == "running",
        ).limit(1)
    )
    return jid is not None


def celery_ping_workers(timeout: float = 2.0) -> tuple[list[dict], bool, str | None]:
    """Return ``(workers, ping_succeeded, error_message)``."""
    from director_api.tasks.celery_app import celery_app

    try:
        result = celery_app.control.ping(timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log.debug("celery_ping_failed", error=str(exc)[:200])
        return [], False, str(exc)[:300]

    workers: list[dict] = []
    for resp in result:
        for name, body in resp.items():
            workers.append({"name": name, "ok": body.get("ok") == "pong"})
    ok = len(workers) > 0 and all(w["ok"] for w in workers)
    return workers, ok, None


def build_celery_status_data(db: Session, tenant_id: str) -> dict:
    """Payload for ``GET /v1/celery/status``."""
    workers, ping_ok, ping_err = celery_ping_workers()
    if ping_ok:
        return {"status": "online", "workers": workers, "liveness": "ping"}

    if tenant_has_running_async_work(db, tenant_id):
        return {
            "status": "online",
            "workers": workers,
            "liveness": "inferred_busy",
            "note": "Worker did not answer control ping; a running job or agent run is still open (normal on Windows solo pool during long tasks).",
        }

    out: dict = {"status": "offline", "workers": workers, "liveness": "ping"}
    if ping_err:
        out["error"] = ping_err
    return out
