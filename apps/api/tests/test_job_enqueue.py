"""Tests for Celery job enqueue helpers."""

from __future__ import annotations

import threading
import time
import uuid
from unittest.mock import MagicMock, patch

from director_api.tasks.job_enqueue import enqueue_job_task


def test_enqueue_job_task_runs_long_jobs_in_background_when_eager():
    started = threading.Event()
    release = threading.Event()
    task = MagicMock()
    task.name = "director.run_phase3_job"

    def _apply_async(*, args, task_id):
        started.set()
        release.wait(timeout=5.0)

    task.apply_async.side_effect = _apply_async
    job_id = uuid.uuid4()

    with patch("director_api.tasks.job_enqueue._should_run_eager_task_async", return_value=True):
        t0 = time.monotonic()
        enqueue_job_task(task, job_id)
        elapsed = time.monotonic() - t0

    assert elapsed < 0.5
    assert started.wait(timeout=2.0)
    release.set()
    task.apply_async.assert_called_once_with(args=[str(job_id)], task_id=str(job_id))


def test_enqueue_job_task_inline_when_not_eager_async():
    task = MagicMock()
    task.name = "director.run_phase3_job"
    job_id = uuid.uuid4()

    with patch("director_api.tasks.job_enqueue._should_run_eager_task_async", return_value=False):
        enqueue_job_task(task, job_id)

    task.apply_async.assert_called_once_with(args=[str(job_id)], task_id=str(job_id))
