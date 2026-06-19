"""Recover jobs left ``running`` after an in-process worker died (e.g. API restart with CELERY_EAGER)."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import Asset, Job

log = structlog.get_logger(__name__)

# Jobs started at or after this moment belong to the current API process.
_PROCESS_BOOT_AT = datetime.now(timezone.utc)


def recover_orphaned_running_jobs(db: Session, *, reason: str = "worker_restarted_job_orphaned") -> int:
    """Mark in-flight jobs failed when no worker can still be executing them."""
    now = datetime.now(timezone.utc)
    stale_jobs = list(
        db.scalars(
            select(Job).where(
                Job.status == "running",
                Job.started_at.is_not(None),
                Job.started_at < _PROCESS_BOOT_AT,
            )
        ).all()
    )
    if not stale_jobs:
        running_assets = list(
            db.scalars(
                select(Asset).where(
                    Asset.status == "running",
                    Asset.asset_type.in_(("image", "video")),
                    Asset.created_at < _PROCESS_BOOT_AT,
                )
            ).all()
        )
        if running_assets:
            for asset in running_assets:
                asset.status = "failed"
                asset.error_message = reason[:8000]
                log.warning(
                    "orphaned_asset_recovered",
                    asset_id=str(asset.id),
                    asset_type=asset.asset_type,
                )
            db.commit()
        return 0

    n = 0
    for job in stale_jobs:
        job.status = "failed"
        job.error_message = reason[:8000]
        job.completed_at = now
        n += 1
        log.warning("orphaned_job_recovered", job_id=str(job.id), job_type=job.type, reason=reason)

    running_assets = list(
        db.scalars(
            select(Asset).where(
                Asset.status == "running",
                Asset.asset_type.in_(("image", "video")),
            )
        ).all()
    )
    for asset in running_assets:
        asset.status = "failed"
        asset.error_message = reason[:8000]
        log.warning("orphaned_asset_recovered", asset_id=str(asset.id), asset_type=asset.asset_type)

    if n or running_assets:
        db.commit()
    return n
