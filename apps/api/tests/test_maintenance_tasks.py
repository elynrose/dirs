"""Celery maintenance tasks registration."""

from director_api.tasks.celery_app import celery_app


def test_reap_stale_jobs_task_registered() -> None:
    assert "director.reap_stale_jobs" in celery_app.tasks


def test_reap_stale_agent_runs_task_registered() -> None:
    assert "director.reap_stale_agent_runs" in celery_app.tasks
