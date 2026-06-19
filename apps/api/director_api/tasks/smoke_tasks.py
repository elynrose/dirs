"""Celery task: adapter smoke test jobs."""

from __future__ import annotations

from director_api.logging_config import get_logger
from director_api.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="director.run_adapter_smoke")
def run_adapter_smoke_task(job_id: str) -> None:
    from director_api.tasks.worker_runtime import run_adapter_smoke_impl

    run_adapter_smoke_impl(job_id)
