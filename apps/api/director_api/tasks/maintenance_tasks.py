"""Celery maintenance tasks — beat-scheduled housekeeping jobs.

Extracted from worker_tasks.py (§ split plan).
These tasks have no dependencies on phase-specific business logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from director_api.config import get_settings
from director_api.db.models import AgentRun, Job
from director_api.db.session import SessionLocal
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.tasks.celery_app import celery_app
from director_api.services.project_ideas import process_due_scheduled_idea_runs

log = structlog.get_logger(__name__)


@celery_app.task(name="director.reap_stale_jobs")
def reap_stale_jobs() -> dict[str, Any]:
    """Mark long-running jobs failed (Phase 6 stale-task policy).

    Any job still in ``running`` state after ``stale_job_minutes`` without
    a heartbeat is assumed to have lost its worker and is marked ``failed``
    so the UI can surface the error and operators can retry.
    """
    with SessionLocal() as db:
        settings = resolve_runtime_settings(db, get_settings())
        minutes = max(5, int(settings.stale_job_minutes))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        stale_jobs = list(
            db.scalars(
                select(Job).where(
                    Job.status == "running",
                    Job.started_at.is_not(None),
                    Job.started_at < cutoff,
                )
            ).all()
        )
        n = len(stale_jobs)
        for j in stale_jobs:
            j.status = "failed"
            j.error_message = f"stale_job_reaped_after_{minutes}m"
            j.completed_at = datetime.now(timezone.utc)
        if n:
            db.commit()
    return {"reaped": n, "stale_after_minutes": minutes}


@celery_app.task(name="director.reap_stale_agent_runs")
def reap_stale_agent_runs() -> dict[str, Any]:
    """Mark long-``running`` agent runs ``failed`` when the worker likely died (no completion).

    Uses the same ``stale_job_minutes`` window as :func:`reap_stale_jobs`.  Only non-terminal
    ``running`` rows are touched (``queued`` is left alone to avoid racing an in-flight Celery
    delivery; ``paused`` is left alone so deliberate pauses are not auto-failed).
    """
    with SessionLocal() as db:
        settings = resolve_runtime_settings(db, get_settings())
        minutes = max(5, int(settings.stale_job_minutes))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        heartbeat = func.coalesce(AgentRun.started_at, AgentRun.updated_at, AgentRun.created_at)
        stale_runs = list(
            db.scalars(
                select(AgentRun).where(
                    AgentRun.status == "running",
                    heartbeat < cutoff,
                )
            ).all()
        )
        n = len(stale_runs)
        now = datetime.now(timezone.utc)
        for r in stale_runs:
            r.status = "failed"
            r.error_message = f"stale_agent_run_reaped_after_{minutes}m"
            r.completed_at = now
            r.block_code = "stale_run"
            r.block_message = "No worker progress within the stale window; marked failed by housekeeping."
            ev = list(r.steps_json) if r.steps_json else []
            ev.append(
                {
                    "step": "pipeline",
                    "status": "failed",
                    "at": now.isoformat(),
                    "reason": "stale_agent_run_reaped",
                    "detail": {"stale_after_minutes": minutes},
                }
            )
            r.steps_json = ev
            flag_modified(r, "steps_json")
        if n:
            db.commit()
            for r in stale_runs:
                log.warning(
                    "stale_agent_run_reaped",
                    agent_run_id=str(r.id),
                    project_id=str(r.project_id),
                    stale_after_minutes=minutes,
                )
    return {"reaped_agent_runs": n, "stale_after_minutes": minutes}


@celery_app.task(name="director.process_due_idea_schedules")
def process_due_idea_schedules() -> dict[str, Any]:
    """Fire agent runs for idea rows whose ``scheduled_at`` is due (requires Celery beat)."""
    with SessionLocal() as db:
        return process_due_scheduled_idea_runs(db)
