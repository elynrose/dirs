"""Large timeline + scene graph: ``compute_phase5_readiness`` bounded runtime (Postgres)."""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from director_api.config import get_settings
from director_api.db.models import Asset, Chapter, Project, Scene, TimelineVersion
from director_api.db.session import SessionLocal
from director_api.services.phase5_readiness import compute_phase5_readiness
from director_api.validation.timeline_schema import validate_timeline_document

from .conftest import stress_integration_enabled, stress_scene_count

pytestmark = [pytest.mark.stress, pytest.mark.skipif(not stress_integration_enabled(), reason="Set STRESS_INTEGRATION=1")]


def test_phase5_readiness_many_clips_rough_cut(tmp_path: Path) -> None:
    """Creates N scenes with assets + timeline clips; asserts readiness returns within budget."""
    settings = get_settings()
    if "postgresql" not in settings.database_url.lower():
        pytest.skip("Stress DB test expects PostgreSQL (see DATABASE_URL)")

    n = stress_scene_count(80)
    max_sec = float(os.environ.get("STRESS_READINESS_MAX_SEC", "45"))

    tenant_id = f"stress-{uuid.uuid4().hex[:12]}"
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    project_id = uuid.uuid4()
    chapter_id = uuid.uuid4()
    tv_id = uuid.uuid4()

    with SessionLocal() as db:
        proj = Project(
            id=project_id,
            tenant_id=tenant_id,
            title="stress project",
            topic="stress",
            target_runtime_minutes=60,
        )
        ch = Chapter(
            id=chapter_id,
            project_id=project_id,
            order_index=0,
            title="Ch1",
        )
        db.add(proj)
        db.add(ch)
        db.flush()

        clips: list[dict] = []
        now = datetime.now(timezone.utc)
        for i in range(n):
            sc_id = uuid.uuid4()
            ast_id = uuid.uuid4()
            rel_key = f"assets/{project_id}/{sc_id}/still-{i}.jpg"
            fp = storage_root / rel_key
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(b"\xff\xd8\xff\xd9")  # minimal JPEG SOI/EOI

            sc = Scene(
                id=sc_id,
                chapter_id=chapter_id,
                order_index=i,
                narration_text="",
            )
            db.add(sc)
            db.add(
                Asset(
                    id=ast_id,
                    tenant_id=tenant_id,
                    scene_id=sc_id,
                    project_id=project_id,
                    asset_type="image",
                    status="succeeded",
                    storage_url=rel_key,
                    approved_at=now,
                )
            )
            clips.append(
                {
                    "order_index": i,
                    "source": {"kind": "asset", "asset_id": str(ast_id)},
                    "duration_sec": 3.0,
                }
            )

        tdoc = {"schema_version": 2, "clips": clips}
        validate_timeline_document(tdoc)
        tv = TimelineVersion(
            id=tv_id,
            tenant_id=tenant_id,
            project_id=project_id,
            version_name="stress",
            timeline_json=tdoc,
        )
        db.add(tv)
        db.commit()

        t0 = time.perf_counter()
        r = compute_phase5_readiness(
            db,
            project_id=project_id,
            tenant_id=tenant_id,
            timeline_version_id=tv_id,
            storage_root=storage_root,
            export_stage="rough_cut",
            allow_unapproved_media=False,
        )
        elapsed = time.perf_counter() - t0

        db.delete(proj)
        db.commit()

    assert elapsed < max_sec, f"readiness took {elapsed:.2f}s (limit {max_sec}s) for n={n}"
    assert r.get("error") != "project_not_found"
    assert isinstance(r.get("issues"), list)
