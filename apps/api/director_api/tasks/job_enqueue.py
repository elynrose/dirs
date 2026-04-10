"""Enqueue Celery tasks with task_id == Job.id so cancel can revoke by job UUID."""

from __future__ import annotations

from uuid import UUID

from celery import Task


def enqueue_job_task(task: Task, job_id: UUID) -> None:
    task.apply_async(args=[str(job_id)], task_id=str(job_id))


def enqueue_run_phase3_job(job_id: UUID) -> None:
    """Lazy-import ``run_phase3_job`` so API routers avoid loading ``worker_tasks`` at import time."""
    from director_api.tasks.worker_tasks import run_phase3_job

    enqueue_job_task(run_phase3_job, job_id)
