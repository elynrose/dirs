"""Celery entrypoints for Phase 3 jobs (re-export from ``worker_tasks``)."""

from director_api.tasks.worker_tasks import run_phase3_job

__all__ = ["run_phase3_job"]
