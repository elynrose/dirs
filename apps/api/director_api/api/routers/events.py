"""Server-Sent Events endpoint — GET /v1/events.

The Studio frontend subscribes once per open project and receives a stream of
change events so it can update the UI without maintaining multiple polling loops.

Event types emitted
-------------------
  jobs_update          Active job list changed (new job, status transition, removal). Payload matches
                       ``GET /v1/projects/{id}/jobs/active`` (includes ``payload`` / ``result``) so the
                       Studio can refresh the right scene when a job finishes.
  agent_run_update     Agent run status or current_step changed.
  asset_ready          A scene asset reached `succeeded` status.
  celery_status        Celery worker online/offline state changed (polled every 30 s). On Windows
                       ``--pool=solo``, ping may fail while a task runs; we then infer *online*
                       if the tenant still has a running agent run or Studio job.

Protocol
--------
Each event is a standard SSE frame:

  event: <type>\\n
  data: <json-payload>\\n
  \\n

The connection is kept alive with a ``: keep-alive`` comment every 15 seconds.
Clients should reconnect automatically (EventSource does this by default).

Rate / resource considerations
-------------------------------
The server polls the database at ``POLL_INTERVAL_SEC`` (default 1.5 s) per
connection.  Connections are short-lived (browser closes and re-opens on
project switch).  The Celery status check uses a 2-second Celery ping (in a
thread pool) plus a DB fallback when ping fails during long solo-pool work.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator
from uuid import UUID

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from director_api.api.schemas.project import JobOut
from director_api.auth.deps import extract_token
from director_api.auth.jwtutil import decode_access_token
from director_api.config import Settings, get_settings
from director_api.services.celery_liveness import celery_ping_workers, tenant_has_running_async_work
from director_api.db.models import AgentRun, Asset, Job, Project, TenantMembership
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.db.session import get_db

router = APIRouter(tags=["events"])
log = structlog.get_logger(__name__)

# How often to query the DB for state changes (seconds).
_POLL_INTERVAL_SEC = 1.5
# How often to check Celery worker status (seconds).
_CELERY_POLL_SEC = 30
# Maximum connection lifetime (seconds) — client reconnects after this.
_MAX_STREAM_SEC = 300


def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _keepalive() -> str:
    return ": keep-alive\n\n"


def _snapshot_active_jobs(db: Session, project_id: UUID, tenant_id: str) -> list[dict]:
    rows = list(
        db.scalars(
            select(Job)
            .where(
                and_(
                    Job.project_id == project_id,
                    Job.tenant_id == tenant_id,
                    Job.status.in_(("queued", "running")),
                )
            )
            .order_by(desc(Job.created_at))
            .limit(100)
        ).all()
    )
    # Match JobOut / HTTP active-jobs list so the client can read payload.scene_id when a job drops off.
    return [JobOut.model_validate(j).model_dump(mode="json") for j in rows]


def _snapshot_agent_run(db: Session, project_id: UUID, tenant_id: str) -> dict | None:
    run = db.scalar(
        select(AgentRun)
        .where(
            AgentRun.project_id == project_id,
            AgentRun.tenant_id == tenant_id,
        )
        .order_by(desc(AgentRun.created_at))
        .limit(1)
    )
    if not run:
        return None
    return {
        "id": str(run.id),
        "status": run.status,
        "current_step": run.current_step,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }


def _snapshot_recent_assets(db: Session, project_id: UUID, tenant_id: str, since_ts: float) -> list[dict]:
    """Return succeeded assets created after `since_ts` (unix timestamp)."""
    from datetime import datetime, timezone

    cutoff = datetime.fromtimestamp(since_ts, tz=timezone.utc)
    rows = list(
        db.scalars(
            select(Asset)
            .where(
                Asset.project_id == project_id,
                Asset.tenant_id == tenant_id,
                Asset.status == "succeeded",
                Asset.created_at >= cutoff,
            )
            .order_by(desc(Asset.created_at))
            .limit(20)
        ).all()
    )
    return [
        {
            "id": str(a.id),
            "scene_id": str(a.scene_id) if a.scene_id else None,
            "asset_type": a.asset_type,
            "status": a.status,
        }
        for a in rows
    ]


def _celery_ping_ok_sync() -> bool:
    _workers, ok, _err = celery_ping_workers()
    return ok


async def _event_stream(
    project_id: UUID,
    settings: Settings,
    db: Session,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE frames for the lifetime of the connection."""
    tenant_id = settings.default_tenant_id
    deadline = time.monotonic() + _MAX_STREAM_SEC
    last_keepalive = time.monotonic()
    last_celery_check = 0.0
    asset_scan_since = time.time()

    # Initial snapshots to detect first-change diffs.
    prev_jobs: list[dict] = []
    prev_run: dict | None = None
    prev_celery: bool | None = None

    # Send an initial hello so the client knows the connection is live.
    yield _sse("connected", {"project_id": str(project_id)})

    while time.monotonic() < deadline:
        now_mono = time.monotonic()

        # --- jobs ---
        try:
            jobs = _snapshot_active_jobs(db, project_id, tenant_id)
            if jobs != prev_jobs:
                yield _sse("jobs_update", {"jobs": jobs, "count": len(jobs)})
                prev_jobs = jobs
        except Exception as exc:
            log.debug("sse_jobs_poll_error", error=str(exc)[:200])

        # --- agent run ---
        try:
            run = _snapshot_agent_run(db, project_id, tenant_id)
            if run != prev_run:
                yield _sse("agent_run_update", {"run": run})
                prev_run = run
        except Exception as exc:
            log.debug("sse_run_poll_error", error=str(exc)[:200])

        # --- new assets ---
        try:
            new_assets = _snapshot_recent_assets(db, project_id, tenant_id, asset_scan_since)
            if new_assets:
                for asset in new_assets:
                    yield _sse("asset_ready", {"asset": asset})
                asset_scan_since = time.time()
        except Exception as exc:
            log.debug("sse_asset_poll_error", error=str(exc)[:200])

        # --- celery status (infrequent) ---
        if now_mono - last_celery_check >= _CELERY_POLL_SEC:
            try:
                loop = asyncio.get_running_loop()
                ping_ok = await loop.run_in_executor(None, _celery_ping_ok_sync)
                online = ping_ok or tenant_has_running_async_work(db, tenant_id)
                if online != prev_celery:
                    yield _sse("celery_status", {"online": online})
                    prev_celery = online
            except Exception as exc:
                log.debug("sse_celery_poll_error", error=str(exc)[:200])
            last_celery_check = now_mono

        # --- keep-alive comment (every 15 s) ---
        if now_mono - last_keepalive >= 15:
            yield _keepalive()
            last_keepalive = now_mono

        await asyncio.sleep(_POLL_INTERVAL_SEC)

    # Graceful EOF — client's EventSource will reconnect.
    yield _sse("stream_end", {"reason": "max_lifetime_reached"})


@router.get("/events")
async def project_event_stream(
    request: Request,
    project_id: UUID = Query(..., description="Project UUID to subscribe to"),
    db: Session = Depends(get_db),
):
    """Subscribe to a real-time event stream for a project.

    Returns a ``text/event-stream`` response (Server-Sent Events).  The browser's
    built-in ``EventSource`` API handles reconnection automatically.

    **Rate:** approximately one DB query per poll interval (1.5 s) per open connection.
    The rate limiter skips ``/v1/events`` to avoid counting the long-lived connection
    as many separate requests.

    Authorization uses the project row's ``tenant_id`` plus JWT membership, so a stale
    ``tenant_id`` query parameter (common after workspace switches) does not cause 404.
    """
    base = get_settings()
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})

    if not base.director_auth_enabled:
        settings = resolve_runtime_settings(db, base, base.default_tenant_id)
        if p.tenant_id != settings.default_tenant_id:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    else:
        token = extract_token(request, base)
        if not token:
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "missing credentials"},
            )
        try:
            claims = decode_access_token(base, token)
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "invalid or expired token"},
            ) from None
        try:
            user_id = int(str(claims["sub"]).strip())
        except (KeyError, ValueError, TypeError):
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "invalid token subject"},
            )
        row = db.scalar(
            select(TenantMembership).where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == p.tenant_id,
            )
        )
        if row is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "FORBIDDEN",
                    "message": "not a member of this project's workspace",
                },
            )
        settings = resolve_runtime_settings(db, base, p.tenant_id)

    return StreamingResponse(
        _event_stream(project_id, settings, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
            "Connection": "keep-alive",
        },
    )
