#!/usr/bin/env python3
"""Enqueue one scene_generate_video job (comfyui_wan) and report result."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
API = REPO / "apps" / "api"
sys.path.insert(0, str(API))
os.chdir(API)

from sqlalchemy import select, text

from director_api.config import get_settings
from director_api.db.models import Chapter, Job, Scene
from director_api.db.session import SessionLocal
from director_api.tasks.job_enqueue import enqueue_run_phase3_job


def main() -> int:
    s = get_settings()
    with SessionLocal() as db:
        sc = db.scalars(select(Scene).limit(1)).first()
        if not sc:
            print("no scenes")
            return 1
        ch = db.get(Chapter, sc.chapter_id)
        assert ch
        j = Job(
            id=uuid4(),
            tenant_id=s.default_tenant_id,
            type="scene_generate_video",
            status="queued",
            payload={
                "scene_id": str(sc.id),
                "tenant_id": s.default_tenant_id,
                "generation_tier": "preview",
                "video_provider": "comfyui_wan",
            },
            project_id=ch.project_id,
        )
        db.add(j)
        db.commit()
        print("job_id", j.id, "scene", sc.id)
        enqueue_run_phase3_job(j.id)
        db.refresh(j)
        row = db.execute(
            text(
                "select provider, status, left(coalesce(error_message,''),120) "
                "from assets where scene_id=:sid and asset_type='video' "
                "order by created_at desc limit 1"
            ),
            {"sid": str(sc.id)},
        ).first()
        print("job_status", j.status, "error", (j.error_message or "")[:120])
        print("latest_video_asset", row)
    return 0 if row and row[1] == "succeeded" and row[0] == "comfyui_wan" else 1


if __name__ == "__main__":
    raise SystemExit(main())
