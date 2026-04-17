"""Celery duplicate-delivery semantics for ``acquire_job_for_work`` (retry stress)."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from director_api.db.models import Job
from director_api.services.job_worker_gate import acquire_job_for_work

pytestmark = pytest.mark.stress


def test_acquire_transitions_queued_to_running() -> None:
    job = MagicMock(spec=Job)
    job.id = uuid4()
    job.status = "queued"
    job.started_at = None
    db = MagicMock()
    db.refresh = MagicMock()

    assert acquire_job_for_work(db, job) is True
    assert job.status == "running"
    assert job.started_at is not None
    db.commit.assert_called_once()


def test_acquire_skips_duplicate_when_already_running() -> None:
    job = MagicMock(spec=Job)
    job.id = uuid4()
    job.status = "running"
    db = MagicMock()
    db.refresh = MagicMock()

    assert acquire_job_for_work(db, job) is False
    db.commit.assert_not_called()


def test_acquire_skips_terminal_states() -> None:
    for st in ("succeeded", "failed", "cancelled"):
        job = MagicMock(spec=Job)
        job.id = uuid4()
        job.status = st
        db = MagicMock()
        db.refresh = MagicMock()
        assert acquire_job_for_work(db, job) is False
        db.commit.assert_not_called()
