"""Celery application — broker config, queues, and beat schedule.

Named queues
------------
Three queues with dedicated worker routing allow independent scaling and
prevent long-running media jobs from starving fast text jobs:

  text    — Phase 2 (research/script) + Phase 4 (critic) + agent orchestration
  media   — Phase 3 image/video generation (GPU-bound, long wall time)
  compile — Phase 5 FFmpeg compile (disk/CPU-bound, single concurrent job)

Start workers per queue:

  celery -A director_api.tasks.celery_app worker -Q text,media,compile -l info   # single-node all queues
  celery -A director_api.tasks.celery_app worker -Q media -l info                 # GPU-only worker
  celery -A director_api.tasks.celery_app worker -Q text,compile -l info          # CPU-only worker
"""

from __future__ import annotations

import sys

from celery import Celery
from celery.schedules import crontab

from director_api.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "director",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
)

_worker_pool = "solo" if sys.platform == "win32" else None

celery_app.conf.update(
    # Serialisation — no pickle (security).
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    timezone="UTC",
    task_track_started=True,

    # Default time limits for tasks that do not set their own (e.g. reap_stale_jobs).
    # Keep well above typical beat-task wall time.  On Windows --pool=solo a hard
    # limit can kill the *entire* worker process, so be conservative.
    task_time_limit=1800,
    task_soft_time_limit=1500,

    # Result expiry — prevent Redis from accumulating stale task results indefinitely.
    result_expires=86400,  # 24 hours

    # Named queues — tasks declare their queue; workers can subscribe to subsets.
    task_default_queue="text",
    task_queues={
        "text": {"exchange": "text", "routing_key": "text"},
        "media": {"exchange": "media", "routing_key": "media"},
        "compile": {"exchange": "compile", "routing_key": "compile"},
    },
    task_routes={
        "director.run_phase2_job": {"queue": "text"},
        "director.run_agent_run": {"queue": "text"},
        "director.run_phase4_job": {"queue": "text"},
        "director.run_adapter_smoke": {"queue": "text"},
        "director.run_phase3_job": {"queue": "media"},
        "director.run_phase5_job": {"queue": "compile"},
        "director.reap_stale_jobs": {"queue": "text"},
        "director.reap_stale_agent_runs": {"queue": "text"},
        "director.process_due_idea_schedules": {"queue": "text"},
    },

    **({"worker_pool": _worker_pool} if _worker_pool else {}),
)

if _settings.celery_eager:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

celery_app.conf.beat_schedule = {
    "reap-stale-jobs": {
        "task": "director.reap_stale_jobs",
        "schedule": crontab(minute="*/15"),
    },
    "reap-stale-agent-runs": {
        "task": "director.reap_stale_agent_runs",
        "schedule": crontab(minute="*/15"),
    },
    "process-due-idea-schedules": {
        "task": "director.process_due_idea_schedules",
        "schedule": crontab(minute="*"),
    },
}

# Register tasks — import order matters: maintenance first so the beat task
# is always available even if worker_tasks fails to import.
from director_api.tasks import maintenance_tasks as _maintenance_tasks  # noqa: E402, F401
from director_api.tasks import worker_tasks as _worker_tasks  # noqa: E402, F401
