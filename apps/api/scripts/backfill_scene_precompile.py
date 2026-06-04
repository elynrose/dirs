"""Drain queued scene_precompile jobs and fill cache for timeline assets.

Usage (from apps/api):
  .venv-win\\Scripts\\python.exe scripts/backfill_scene_precompile.py
  .venv-win\\Scripts\\python.exe scripts/backfill_scene_precompile.py --project-id <uuid>
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from director_api.config import get_settings
from director_api.db.models import Asset, Job, Project, TimelineVersion
from director_api.db.session import SessionLocal
from director_api.services.scene_precompile import precompile_mp4_path
from director_api.services.scene_precompile_enqueue import schedule_scene_precompile_for_asset
from director_api.tasks.worker_tasks import run_phase5_job


def _run_queued_precompile_jobs(db, *, project_id: uuid.UUID | None) -> tuple[int, int]:
    q = select(Job).where(Job.type == "scene_precompile", Job.status == "queued")
    if project_id is not None:
        q = q.where(Job.project_id == project_id)
    jobs = db.scalars(q).all()
    ok = skip = 0
    for job in jobs:
        asset_id = (job.payload or {}).get("asset_id")
        asset = db.get(Asset, asset_id) if asset_id else None
        if asset is None or asset.status != "succeeded":
            skip += 1
            continue
        run_phase5_job(str(job.id))
        db.expire_all()
        refreshed = db.get(Job, job.id)
        if refreshed and refreshed.status == "succeeded":
            ok += 1
        else:
            skip += 1
    return ok, skip


def _schedule_timeline_assets(db, settings, *, project_id: uuid.UUID) -> int:
    tv = db.scalars(
        select(TimelineVersion)
        .where(TimelineVersion.project_id == project_id)
        .order_by(TimelineVersion.created_at.desc())
        .limit(1)
    ).first()
    if tv is None or not isinstance(tv.timeline_json, dict):
        return 0
    clips = tv.timeline_json.get("clips")
    if not isinstance(clips, list):
        return 0
    scheduled = 0
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        src = clip.get("source")
        if not isinstance(src, dict) or src.get("kind") != "asset":
            continue
        try:
            aid = uuid.UUID(str(src.get("asset_id")))
        except (ValueError, TypeError):
            continue
        asset = db.get(Asset, aid)
        if asset is None or asset.status != "succeeded":
            continue
        dur = clip.get("duration_sec")
        duration_sec: float | None
        if dur is not None:
            try:
                duration_sec = float(dur)
            except (TypeError, ValueError):
                duration_sec = None
        else:
            duration_sec = None
        if schedule_scene_precompile_for_asset(
            db, settings, asset, duration_sec=duration_sec
        ):
            scheduled += 1
    db.commit()
    return scheduled


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill scene precompile cache")
    parser.add_argument("--project-id", type=str, default="", help="Limit to one project UUID")
    args = parser.parse_args()
    settings = get_settings()
    project_id = uuid.UUID(args.project_id) if args.project_id.strip() else None

    db = SessionLocal()
    try:
        if project_id is None:
            print("Draining all queued scene_precompile jobs…")
        else:
            proj = db.get(Project, project_id)
            print(f"Project: {proj.title if proj else project_id}")

        ok, skip = _run_queued_precompile_jobs(db, project_id=project_id)
        print(f"Ran queued jobs: {ok} succeeded, {skip} skipped/failed")

        if project_id is not None:
            n = _schedule_timeline_assets(db, settings, project_id=project_id)
            print(f"Scheduled {n} new precompile job(s) for latest timeline")
            ok2, skip2 = _run_queued_precompile_jobs(db, project_id=project_id)
            print(f"Second pass: {ok2} succeeded, {skip2} skipped/failed")

            mp4s = list((Path(settings.local_storage_root) / "precompiled" / str(project_id)).glob("*.mp4"))
            print(f"Precompiled MP4s on disk: {len(mp4s)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
