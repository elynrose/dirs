"""Enqueue background scene precompile jobs when visual assets are ready."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import Asset, Job
from director_api.services.scene_precompile import (
    default_duration_sec_for_asset,
    invalidate_precompiles_for_scene,
    precompile_is_current,
    precompile_storage_fingerprint_for_asset,
)
from director_api.tasks.job_enqueue import enqueue_run_phase5_job

log = structlog.get_logger(__name__)

# Celery eager mode runs tasks in-process immediately. Enqueue must happen only after the
# Job row is committed; otherwise run_phase5_job opens a new DB session and cannot see it.
_PENDING_PHASE5_ENQUEUE_KEY = "_pending_phase5_enqueue_job_ids"


def _defer_phase5_enqueue(session: Session, job_id: uuid.UUID) -> None:
    pending: list[uuid.UUID] = session.info.setdefault(_PENDING_PHASE5_ENQUEUE_KEY, [])
    pending.append(job_id)


def _flush_pending_phase5_enqueues(session: Session) -> None:
    pending: list[uuid.UUID] = session.info.pop(_PENDING_PHASE5_ENQUEUE_KEY, [])
    for job_id in pending:
        enqueue_run_phase5_job(job_id)


@event.listens_for(Session, "after_commit")
def _enqueue_phase5_jobs_after_commit(session: Session) -> None:
    _flush_pending_phase5_enqueues(session)


@event.listens_for(Session, "after_rollback")
def _drop_phase5_jobs_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_PHASE5_ENQUEUE_KEY, None)


def _precompile_enabled(settings: Settings) -> bool:
    return bool(getattr(settings, "scene_precompile_enabled", True))


def _running_precompile_for_asset(db: Session, tenant_id: str, asset_id: uuid.UUID) -> bool:
    aid = str(asset_id)
    jobs = db.scalars(
        select(Job)
        .where(
            Job.tenant_id == tenant_id,
            Job.type == "scene_precompile",
            Job.status.in_(("queued", "running")),
        )
        .limit(32)
    ).all()
    return any(str((j.payload or {}).get("asset_id") or "") == aid for j in jobs)


def schedule_scene_precompile_for_asset(
    db: Session,
    settings: Settings,
    asset: Asset,
    *,
    duration_sec: float | None = None,
) -> uuid.UUID | None:
    """
    Queue a background ``scene_precompile`` job when a scene image/video asset succeeds.

    Returns new job id, or None if skipped (disabled, wrong type, or already current).
    """
    if not _precompile_enabled(settings):
        return None
    if asset.status != "succeeded" or asset.asset_type not in ("image", "video"):
        return None
    if not asset.storage_url or not asset.project_id:
        return None

    from pathlib import Path

    storage_root = Path(settings.local_storage_root).resolve()
    if duration_sec is None:
        duration_sec = default_duration_sec_for_asset(
            asset, settings, storage_root=storage_root
        )
    fp = precompile_storage_fingerprint_for_asset(asset)
    if precompile_is_current(
        storage_root=storage_root,
        project_id=asset.project_id,
        asset_id=asset.id,
        fingerprint=fp,
        clip_duration_sec=float(duration_sec),
    ):
        return None

    tenant_id = str(asset.tenant_id or "").strip()
    if not tenant_id:
        raise ValueError("asset missing tenant_id")
    if _running_precompile_for_asset(db, tenant_id, asset.id):
        return None

    if asset.scene_id:
        invalidate_precompiles_for_scene(
            storage_root,
            asset.project_id,
            asset.scene_id,
            keep_asset_id=asset.id,
        )

    job = Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="scene_precompile",
        status="queued",
        payload={
            "asset_id": str(asset.id),
            "scene_id": str(asset.scene_id) if asset.scene_id else None,
            "project_id": str(asset.project_id),
            "tenant_id": tenant_id,
            "duration_sec": float(duration_sec),
            "fingerprint": fp,
        },
        project_id=asset.project_id,
    )
    db.add(job)
    db.flush()
    _defer_phase5_enqueue(db, job.id)
    log.info(
        "scene_precompile_enqueued",
        job_id=str(job.id),
        asset_id=str(asset.id),
        scene_id=str(asset.scene_id) if asset.scene_id else None,
    )
    return job.id


def schedule_precompile_for_timeline(
    db: Session,
    settings: Settings,
    *,
    project_id: uuid.UUID,
    timeline_json: dict[str, Any],
) -> int:
    """Re-queue precompile when clip duration or asset selection changes on the timeline."""
    from director_api.services.scene_precompile import default_duration_sec_for_asset
    from pathlib import Path

    if not _precompile_enabled(settings):
        return 0
    clips = timeline_json.get("clips") if isinstance(timeline_json, dict) else None
    if not isinstance(clips, list):
        return 0
    storage_root = Path(settings.local_storage_root).resolve()
    n = 0
    for c in clips:
        if not isinstance(c, dict):
            continue
        src = c.get("source")
        if not isinstance(src, dict) or src.get("kind") != "asset":
            continue
        try:
            aid = uuid.UUID(str(src.get("asset_id")))
        except (ValueError, TypeError):
            continue
        asset = db.get(Asset, aid)
        if asset is None or asset.project_id != project_id:
            continue
        dur = c.get("duration_sec")
        duration_sec: float | None
        if dur is not None:
            try:
                duration_sec = float(dur)
            except (TypeError, ValueError):
                duration_sec = None
        else:
            duration_sec = default_duration_sec_for_asset(
                asset, settings, storage_root=storage_root
            )
        if schedule_scene_precompile_for_asset(
            db, settings, asset, duration_sec=duration_sec
        ):
            n += 1
    return n


def schedule_scene_precompile_for_scene_assets(
    db: Session,
    settings: Settings,
    *,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
) -> int:
    """Re-queue precompile for all succeeded visual assets on a scene (e.g. timeline clip edit)."""
    if not _precompile_enabled(settings):
        return 0
    assets = db.scalars(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.scene_id == scene_id,
            Asset.status == "succeeded",
            Asset.asset_type.in_(("image", "video")),
        )
    ).all()
    n = 0
    for asset in assets:
        if schedule_scene_precompile_for_asset(db, settings, asset) is not None:
            n += 1
    return n
