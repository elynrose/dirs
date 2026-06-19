"""Orphan job recovery on API startup."""

from __future__ import annotations

import uuid

import pytest

from director_api.config import get_settings
from director_api.db.models import Job
from director_api.db.session import SessionLocal
from director_api.services.job_orphan_recovery import recover_orphaned_running_jobs


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    from director_api.db.models import Base
    from director_api.db.session import engine

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        yield db


def test_recover_orphaned_running_jobs_marks_running_jobs_failed(db_session) -> None:
    from datetime import timedelta

    from director_api.services import job_orphan_recovery as mod

    settings = get_settings()
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="scene_generate_video",
        status="running",
        payload={"scene_id": "00000000-0000-0000-0000-000000000001"},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    job.started_at = mod._PROCESS_BOOT_AT - timedelta(minutes=5)
    db_session.commit()

    n = recover_orphaned_running_jobs(db_session)
    assert n == 1
    db_session.refresh(job)
    assert job.status == "failed"
    assert "worker_restarted" in (job.error_message or "")
