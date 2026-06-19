"""Agent run orphan recovery and zombie prevention."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from director_api.db.models import AgentRun, Project
from director_api.services import agent_run_orphan_recovery as mod
from director_api.services.agent_run_orphan_recovery import (
    current_worker_instance_id,
    is_orphaned_agent_run,
    recover_orphaned_agent_runs,
    stamp_agent_run_worker,
    supersede_active_agent_runs_for_project,
)


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    from director_api.db.models import Base
    from director_api.db.session import SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        yield db


def test_supersede_active_agent_runs_for_project(db_session) -> None:
    p = Project(
        id=uuid.uuid4(),
        tenant_id="00000000-0000-0000-0000-000000000001",
        title="T",
        topic="topic",
        status="draft",
        target_runtime_minutes=5,
    )
    db_session.add(p)
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=p.tenant_id,
        project_id=p.id,
        status="running",
        current_step="auto_scene_coverage",
        steps_json=[],
        pipeline_options_json={},
        pipeline_control_json={},
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(run)
    db_session.commit()

    rows = supersede_active_agent_runs_for_project(
        db_session, project_id=p.id, tenant_id=p.tenant_id
    )
    db_session.commit()
    db_session.refresh(run)

    assert len(rows) == 1
    assert run.status == "cancelled"
    assert run.completed_at is not None


def test_is_orphaned_when_worker_instance_mismatch(db_session) -> None:
    p = Project(
        id=uuid.uuid4(),
        tenant_id="00000000-0000-0000-0000-000000000001",
        title="T",
        topic="topic",
        status="draft",
        target_runtime_minutes=5,
    )
    db_session.add(p)
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=p.tenant_id,
        project_id=p.id,
        status="running",
        steps_json=[],
        pipeline_options_json={},
        pipeline_control_json={"worker_instance_id": "dead-worker-id"},
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    assert is_orphaned_agent_run(run) is True


def test_stamped_run_not_orphaned_on_same_worker(db_session) -> None:
    p = Project(
        id=uuid.uuid4(),
        tenant_id="00000000-0000-0000-0000-000000000001",
        title="T",
        topic="topic",
        status="draft",
        target_runtime_minutes=5,
    )
    db_session.add(p)
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=p.tenant_id,
        project_id=p.id,
        status="running",
        steps_json=[],
        pipeline_options_json={},
        pipeline_control_json={},
        started_at=datetime.now(timezone.utc),
    )
    stamp_agent_run_worker(run)
    db_session.add(run)
    db_session.commit()

    assert is_orphaned_agent_run(run) is False
    assert run.pipeline_control_json["worker_instance_id"] == current_worker_instance_id()


def test_recover_orphaned_agent_runs_skips_current_boot_runs(db_session, monkeypatch) -> None:
    boot = datetime.now(timezone.utc)
    monkeypatch.setattr(mod, "_PROCESS_BOOT_AT", boot)

    p = Project(
        id=uuid.uuid4(),
        tenant_id="00000000-0000-0000-0000-000000000001",
        title="T",
        topic="topic",
        status="draft",
        target_runtime_minutes=5,
    )
    db_session.add(p)
    old = AgentRun(
        id=uuid.uuid4(),
        tenant_id=p.tenant_id,
        project_id=p.id,
        status="running",
        steps_json=[],
        pipeline_options_json={},
        pipeline_control_json={},
        started_at=boot - timedelta(minutes=5),
    )
    fresh = AgentRun(
        id=uuid.uuid4(),
        tenant_id=p.tenant_id,
        project_id=p.id,
        status="running",
        steps_json=[],
        pipeline_options_json={},
        pipeline_control_json={},
        started_at=boot + timedelta(seconds=1),
    )
    stamp_agent_run_worker(fresh)
    db_session.add_all([old, fresh])
    db_session.commit()

    recover_orphaned_agent_runs(db_session)
    db_session.refresh(old)
    db_session.refresh(fresh)

    assert old.status == "cancelled"
    assert fresh.status == "running"
