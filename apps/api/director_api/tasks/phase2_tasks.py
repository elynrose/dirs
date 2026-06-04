"""Celery entrypoints for Phase 2 jobs (re-export from ``worker_tasks``)."""

from director_api.tasks.worker_tasks import run_phase2_job

__all__ = ["run_phase2_job"]
