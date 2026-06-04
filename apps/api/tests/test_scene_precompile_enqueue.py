"""Tests for deferred scene precompile enqueue (commit before Celery)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from director_api.services import scene_precompile_enqueue as spe


def test_phase5_enqueue_runs_after_commit_not_before() -> None:
    session = MagicMock()
    session.info = {}
    job_id = uuid.uuid4()
    enqueued: list[uuid.UUID] = []

    with patch.object(spe, "enqueue_run_phase5_job", side_effect=lambda jid: enqueued.append(jid)):
        spe._defer_phase5_enqueue(session, job_id)
        assert enqueued == []
        spe._flush_pending_phase5_enqueues(session)
        assert enqueued == [job_id]
        assert session.info.get(spe._PENDING_PHASE5_ENQUEUE_KEY) is None


def test_phase5_enqueue_dropped_on_rollback() -> None:
    session = MagicMock()
    session.info = {spe._PENDING_PHASE5_ENQUEUE_KEY: [uuid.uuid4()]}
    spe._drop_phase5_jobs_after_rollback(session)
    assert spe._PENDING_PHASE5_ENQUEUE_KEY not in session.info
