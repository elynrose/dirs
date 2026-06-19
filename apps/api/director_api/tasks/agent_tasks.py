"""Celery task: agent run orchestration."""

from __future__ import annotations

from director_api.config import get_settings
from director_api.logging_config import get_logger
from director_api.tasks.agent_run_control import AgentRunPausedYield
from director_api.tasks.celery_app import celery_app

log = get_logger(__name__)

_CELERY_AGENT_RUN_SOFT_SEC = 14_400
_CELERY_AGENT_RUN_HARD_SEC = 15_300


@celery_app.task(
    name="director.run_agent_run",
    soft_time_limit=_CELERY_AGENT_RUN_SOFT_SEC,
    time_limit=_CELERY_AGENT_RUN_HARD_SEC,
)
def run_agent_run(agent_run_id: str) -> None:
    paused = False
    try:
        from director_api.tasks.agent_impl import _run_agent_run_impl

        _run_agent_run_impl(agent_run_id)
    except AgentRunPausedYield:
        paused = True
        s = get_settings()
        countdown = float(getattr(s, "agent_run_pause_poll_sec", 2.0))
        celery_app.send_task(
            "director.run_agent_run",
            args=[agent_run_id],
            countdown=countdown,
        )
        log.info("agent_run_paused_requeued", agent_run_id=agent_run_id, countdown_sec=countdown)
        return
    finally:
        if not paused:
            try:
                from director_api.services.telegram_notify import telegram_notify_after_agent_run

                telegram_notify_after_agent_run(agent_run_id)
            except Exception as exc:
                log.warning("telegram_notify_after_run_failed", agent_run_id=agent_run_id, error=str(exc))
