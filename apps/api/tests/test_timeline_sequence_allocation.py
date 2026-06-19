"""Timeline sequence allocation for scene assets."""

from __future__ import annotations

import uuid

from director_api.db.models import Asset, Chapter, Project, Scene
from director_api.db.session import SessionLocal
from director_api.tasks.worker_helpers import next_timeline_sequence_for_scene


def test_next_timeline_sequence_increments():
    db = SessionLocal()
    try:
        p = Project(
            id=uuid.uuid4(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            title="Seq test",
            topic="topic",
            status="draft",
            target_runtime_minutes=5,
        )
        ch = Chapter(id=uuid.uuid4(), project_id=p.id, order_index=0, title="Ch")
        sc = Scene(id=uuid.uuid4(), chapter_id=ch.id, order_index=0, narration_text="hello")
        db.add_all([p, ch, sc])
        db.commit()

        assert next_timeline_sequence_for_scene(db, sc.id) == 0
        db.add(
            Asset(
                id=uuid.uuid4(),
                tenant_id=p.tenant_id,
                scene_id=sc.id,
                project_id=p.id,
                asset_type="image",
                status="succeeded",
                timeline_sequence=0,
            )
        )
        db.commit()
        assert next_timeline_sequence_for_scene(db, sc.id) == 1
    finally:
        db.rollback()
        db.close()


def test_next_timeline_sequence_recovers_from_duplicate_max():
    db = SessionLocal()
    try:
        p = Project(
            id=uuid.uuid4(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            title="Seq dup",
            topic="topic",
            status="draft",
            target_runtime_minutes=5,
        )
        ch = Chapter(id=uuid.uuid4(), project_id=p.id, order_index=0, title="Ch")
        sc = Scene(id=uuid.uuid4(), chapter_id=ch.id, order_index=0, narration_text="hello")
        db.add_all([p, ch, sc])
        for _ in range(2):
            db.add(
                Asset(
                    id=uuid.uuid4(),
                    tenant_id=p.tenant_id,
                    scene_id=sc.id,
                    project_id=p.id,
                    asset_type="image",
                    status="succeeded",
                    timeline_sequence=0,
                )
            )
        db.commit()
        assert next_timeline_sequence_for_scene(db, sc.id) == 2
    finally:
        db.rollback()
        db.close()
