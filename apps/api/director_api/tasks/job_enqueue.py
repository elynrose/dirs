"""Enqueue Celery tasks with task_id == Job.id so cancel can revoke by job UUID.

Phase job tasks (``run_phase2_job`` … ``run_phase5_job``) are **lazy-imported** inside the
``enqueue_run_phaseN_job`` helpers so API router modules do not pull ``worker_tasks`` (or heavy
task implementation graphs) at import time. Prefer these helpers over importing task callables
from ``phase*_tasks`` and calling :func:`enqueue_job_task` directly from routers.

For scripts or tests that need the bound Celery task object (e.g. ``task.run()``), import from
``director_api.tasks.phaseN_tasks`` in that script only.
"""

from __future__ import annotations

import threading
from uuid import UUID

from celery import Task

# Celery eager mode executes tasks inline in the caller. Long media/compile jobs would
# block HTTP handlers (e.g. POST generate-video) until ComfyUI finishes — run those in a
# background thread so the API can return 202 immediately.
_EAGER_ASYNC_TASK_NAMES = frozenset(
    {
        "director.run_phase3_job",
        "director.run_phase5_job",
        "director.run_agent_run",
    }
)


def _should_run_eager_task_async(task_name: str) -> bool:
    from director_api.tasks.celery_app import celery_app

    return bool(celery_app.conf.task_always_eager) and task_name in _EAGER_ASYNC_TASK_NAMES


def enqueue_job_task(task: Task, job_id: UUID) -> None:
    args = [str(job_id)]
    tid = str(job_id)
    if _should_run_eager_task_async(task.name):
        threading.Thread(
            target=lambda: task.apply_async(args=args, task_id=tid),
            daemon=True,
            name=f"eager-{task.name}-{tid[:8]}",
        ).start()
        return
    task.apply_async(args=args, task_id=tid)


def enqueue_run_phase2_job(job_id: UUID) -> None:
    from director_api.tasks.phase2_tasks import run_phase2_job

    enqueue_job_task(run_phase2_job, job_id)


def enqueue_run_phase3_job(job_id: UUID) -> None:
    from director_api.tasks.phase3_tasks import run_phase3_job

    enqueue_job_task(run_phase3_job, job_id)


def enqueue_run_phase4_job(job_id: UUID) -> None:
    from director_api.tasks.phase4_tasks import run_phase4_job

    enqueue_job_task(run_phase4_job, job_id)


def enqueue_run_phase5_job(job_id: UUID) -> None:
    from director_api.tasks.phase5_tasks import run_phase5_job

    enqueue_job_task(run_phase5_job, job_id)


def enqueue_agent_run(agent_run_id: UUID) -> None:
    from director_api.tasks.worker_tasks import run_agent_run

    enqueue_job_task(run_agent_run, agent_run_id)
