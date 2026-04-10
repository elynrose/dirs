"""Celery maintenance tasks — beat-scheduled housekeeping jobs.

Extracted from worker_tasks.py (§ split plan).
These tasks have no dependencies on phase-specific business logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from director_api.config import get_settings
from director_api.db.models import Job
from director_api.db.session import SessionLocal
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.tasks.celery_app import celery_app


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
