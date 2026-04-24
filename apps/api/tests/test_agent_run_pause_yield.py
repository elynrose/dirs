"""Agent run pause yields the Celery worker instead of busy-sleeping (solo-pool friendly)."""

import uuid
from unittest.mock import MagicMock

import pytest

from director_api.tasks.worker_tasks import AgentRunPausedYield, _agent_run_checkpoint


def test_checkpoint_raises_yield_when_paused_while_running():
    run_id = uuid.uuid4()
    run = MagicMock()
    run.pipeline_control_json = {"paused": True, "stop_requested": False}
    run.status = "running"
    db = MagicMock()
    db.get = MagicMock(return_value=run)
    db.expire_all = MagicMock()
    db.commit = MagicMock()

    with pytest.raises(AgentRunPausedYield):
        _agent_run_checkpoint(db, run_id)

    assert run.status == "paused"
    db.commit.assert_called()


def test_checkpoint_resumes_when_pipeline_unpaused_but_row_still_paused():
    """UI clears ``paused`` in pipeline_control; checkpoint flips row status back to running."""
    run_id = uuid.uuid4()
    run = MagicMock()
    run.pipeline_control_json = {"paused": False, "stop_requested": False}
    run.status = "paused"
    db = MagicMock()
    db.get = MagicMock(return_value=run)
    db.expire_all = MagicMock()
    db.commit = MagicMock()

    assert _agent_run_checkpoint(db, run_id) == "ok"
    assert run.status == "running"
    db.commit.assert_called()
