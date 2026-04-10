"""Worker-side gate: skip Celery deliveries for cancelled / duplicate / terminal jobs."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy.orm import Session

from director_api.db.models import Job

log = structlog.get_logger(__name__)


def acquire_job_for_work(db: Session, job: Job) -> bool:
    """
    Transition queued -> running when this delivery should execute the job body.
    Returns False if the task should exit without doing work (cancelled, terminal, or duplicate).
    """
    db.refresh(job)
    if job.status == "cancelled":
        log.info("job_skip_cancelled", job_id=str(job.id))
        return False
    if job.status in ("succeeded", "failed"):
        return False
    if job.status == "running":
        log.warning("job_skip_duplicate_delivery", job_id=str(job.id))
        return False
    if job.status != "queued":
        return False

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()
    return True
