"""Enqueue Celery tasks with task_id == Job.id so cancel can revoke by job UUID.

Phase job tasks (``run_phase2_job`` … ``run_phase5_job``) are **lazy-imported** inside the
``enqueue_run_phaseN_job`` helpers so API router modules do not pull ``worker_tasks`` (or heavy
task implementation graphs) at import time. Prefer these helpers over importing task callables
from ``phase*_tasks`` and calling :func:`enqueue_job_task` directly from routers.

For scripts or tests that need the bound Celery task object (e.g. ``task.run()``), import from
``director_api.tasks.phaseN_tasks`` in that script only.
"""

from __future__ import annotations

from uuid import UUID

from celery import Task


def enqueue_job_task(task: Task, job_id: UUID) -> None:
    task.apply_async(args=[str(job_id)], task_id=str(job_id))


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
