"""Celery entrypoints for Phase 5 jobs (re-export from ``worker_tasks``)."""

from director_api.tasks.worker_tasks import run_phase5_job

__all__ = ["run_phase5_job"]
