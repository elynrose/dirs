"""Optional HTTPS webhooks for terminal job states (see docs/webhooks.md).

Delivery policy
---------------
On a non-2xx response or network error the delivery is retried up to
``settings.webhook_max_attempts`` times (default: 3) with exponential
back-off: 1 s, 5 s, 30 s.  Attempts and outcomes are logged at INFO level
so operators can correlate with their receiver's access logs.

HMAC signing
------------
When ``WEBHOOK_SIGNING_SECRET`` is set, every request includes:

  X-Director-Signature: sha256=<hex>
  X-Director-Timestamp: <unix-seconds>

The timestamp is included in the signed payload so replays older than a
configurable window can be rejected on the receiver side.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog

from director_api.config import Settings
from director_api.db.models import Job

log = structlog.get_logger(__name__)

# Backoff delays in seconds between attempts (index 0 = before attempt 2, etc.)
_BACKOFF_SECONDS = (1, 5, 30)


def _infer_job_entity(job: Job) -> tuple[str | None, str | None]:
    p = job.payload or {}
    t = job.type or ""
    if t in ("scene_generate_image", "scene_generate_video"):
        return "scene", p.get("scene_id")
    if t in ("scene_generate", "scene_extend"):
        return "chapter", p.get("chapter_id")
    if t == "scene_critique":
        return "scene", p.get("scene_id")
    if t == "chapter_critique":
        return "chapter", p.get("chapter_id")
    if t == "scene_critic_revision":
        return "scene", p.get("scene_id")
    if t == "narration_generate":
        return "chapter", p.get("chapter_id")
    if t in ("rough_cut", "fine_cut", "final_cut"):
        return "timeline_version", p.get("timeline_version_id")
    if t == "export":
        return "project", p.get("project_id")
    if t == "youtube_upload":
        return "timeline_version", p.get("timeline_version_id")
    if t == "subtitles_generate":
        return "project", p.get("project_id")
    if t in ("research_run", "script_outline", "script_chapters", "script_chapter_regenerate"):
        return "project", p.get("project_id")
    if t == "adapter_smoke":
        return "job", str(job.id)
    return None, None


def _build_payload(job: Job) -> tuple[bytes, str]:
    """Return ``(raw_bytes, event_type)`` for the webhook body."""
    event_type = "job.completed" if job.status == "succeeded" else "job.failed"
    entity_type, entity_id = _infer_job_entity(job)
    body: dict[str, Any] = {
        "id": str(uuid4()),
        "type": event_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "job_id": str(job.id),
            "job_type": job.type,
            "project_id": str(job.project_id) if job.project_id else None,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "status": job.status,
            "result": job.result,
            "error_message": job.error_message,
        },
    }
    raw = json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")
    return raw, event_type


def _build_headers(raw: bytes, settings: Settings) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    secret = (settings.webhook_signing_secret or "").strip().encode("utf-8")
    if secret:
        ts = str(int(time.time()))
        sig_payload = f"{ts}.".encode() + raw
        sig = hmac.new(secret, sig_payload, hashlib.sha256).hexdigest()
        headers["X-Director-Signature"] = f"sha256={sig}"
        headers["X-Director-Timestamp"] = ts
    return headers


def try_deliver_job_webhook(job: Job, settings: Settings) -> None:
    """POST a terminal job event to ``WEBHOOK_URL`` with retry + backoff.

    Never raises — all errors are logged.  On success the HTTP status code
    is logged so operators can confirm receipt.
    """
    url = (settings.webhook_url or "").strip()
    if not url:
        return
    if job.status not in ("succeeded", "failed"):
        return

    raw, event_type = _build_payload(job)
    headers = _build_headers(raw, settings)
    max_attempts = max(1, int(getattr(settings, "webhook_max_attempts", 3)))
    timeout = max(5.0, float(settings.webhook_timeout_sec))

    for attempt in range(1, max_attempts + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, content=raw, headers=headers)

            if r.status_code < 400:
                log.info(
                    "webhook_delivered",
                    job_id=str(job.id),
                    event_type=event_type,
                    http_status=r.status_code,
                    attempt=attempt,
                )
                return

            log.warning(
                "webhook_non_2xx",
                job_id=str(job.id),
                http_status=r.status_code,
                attempt=attempt,
                max_attempts=max_attempts,
                body=r.text[:500],
            )

        except Exception as exc:
            log.warning(
                "webhook_delivery_error",
                job_id=str(job.id),
                attempt=attempt,
                max_attempts=max_attempts,
                error=str(exc)[:300],
            )

        # Back off before the next attempt (no sleep after the final attempt).
        if attempt < max_attempts:
            delay = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]
            log.debug("webhook_retry_backoff", job_id=str(job.id), delay_sec=delay, next_attempt=attempt + 1)
            time.sleep(delay)

    log.error(
        "webhook_delivery_failed_all_attempts",
        job_id=str(job.id),
        event_type=event_type,
        attempts=max_attempts,
    )


def notify_job_terminal(job_id: UUID, settings: Settings) -> None:
    """Load the job after commit and dispatch a webhook if it is in a terminal state."""
    from director_api.db.session import SessionLocal

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status not in ("succeeded", "failed"):
            return
        try_deliver_job_webhook(job, settings)
