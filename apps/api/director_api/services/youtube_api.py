"""YouTube Data API v3 — refresh OAuth token and resumable video upload (httpx)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_START = "https://www.googleapis.com/upload/youtube/v3/videos"


def youtube_refresh_access_token(*, client_id: str, client_secret: str, refresh_token: str) -> str:
    cid = (client_id or "").strip()
    sec = (client_secret or "").strip()
    rt = (refresh_token or "").strip()
    if not cid or not sec or not rt:
        raise ValueError("YouTube OAuth: client_id, client_secret, and refresh_token are required")
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            _TOKEN_URL,
            data={
                "client_id": cid,
                "client_secret": sec,
                "refresh_token": rt,
                "grant_type": "refresh_token",
            },
        )
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube token refresh failed: HTTP {r.status_code} {r.text[:500]}")
    data = r.json()
    tok = (data.get("access_token") or "").strip()
    if not tok:
        raise RuntimeError("YouTube token refresh: no access_token in response")
    return tok


def youtube_upload_mp4_resumable(
    *,
    access_token: str,
    video_path: Path,
    title: str,
    description: str = "",
    privacy_status: str = "unlisted",
    category_id: str = "22",
    timeout_sec: float = 7200.0,
) -> dict[str, Any]:
    """Upload a local MP4 to YouTube; returns ``id``, ``watch_url``."""
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    size = path.stat().st_size
    if size < 64:
        raise ValueError("video file too small")
    snippet = {
        "title": (title or "Untitled")[:100],
        "description": (description or "")[:5000],
        "categoryId": str(category_id or "22"),
    }
    status = {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False}
    body = {"snippet": snippet, "status": status}
    headers = {
        "Authorization": f"Bearer {(access_token or '').strip()}",
        "Content-Type": "application/json",
        "X-Upload-Content-Length": str(size),
        "X-Upload-Content-Type": "video/mp4",
    }
    url = f"{_UPLOAD_START}?uploadType=resumable&part=snippet,status"
    with httpx.Client(timeout=timeout_sec) as client:
        r1 = client.post(url, headers=headers, content=json.dumps(body))
        if r1.status_code not in (200, 201):
            raise RuntimeError(f"YouTube resumable start failed: HTTP {r1.status_code} {r1.text[:800]}")
        loc = (r1.headers.get("location") or r1.headers.get("Location") or "").strip()
        if not loc:
            raise RuntimeError("YouTube resumable start: missing Location header")
        with path.open("rb") as f:
            data = f.read()
        r2 = client.put(
            loc,
            content=data,
            headers={
                "Content-Length": str(size),
                "Content-Type": "video/mp4",
            },
        )
    if r2.status_code not in (200, 201):
        raise RuntimeError(f"YouTube upload PUT failed: HTTP {r2.status_code} {r2.text[:1200]}")
    out = r2.json()
    vid = (out.get("id") or "").strip()
    if not vid:
        raise RuntimeError(f"YouTube upload: unexpected response {out!r}"[:2000])
    watch = f"https://www.youtube.com/watch?v={vid}"
    share = f"https://youtu.be/{vid}"
    return {"id": vid, "watch_url": watch, "share_url": share, "raw": out}


def maybe_enqueue_youtube_upload_after_agent_run(agent_run_id: str) -> None:
    """If workspace has YouTube OAuth + auto-upload, queue ``youtube_upload`` for latest final_cut."""
    import uuid

    from sqlalchemy import select

    from director_api.config import get_settings
    from director_api.db.models import AgentRun, Job, Project, TimelineVersion
    from director_api.db.session import SessionLocal
    from director_api.services.job_quota import assert_can_enqueue
    from director_api.services.runtime_settings import resolve_runtime_settings
    from director_api.tasks.job_enqueue import enqueue_job_task
    from director_api.tasks.worker_tasks import run_phase5_job
    from ffmpeg_pipelines.paths import path_is_readable_file

    try:
        aid = uuid.UUID(agent_run_id)
    except ValueError:
        return
    base = get_settings()
    job_id: uuid.UUID | None = None
    with SessionLocal() as db:
        run = db.get(AgentRun, aid)
        if not run or run.status != "succeeded":
            return
        settings = resolve_runtime_settings(db, base, run.tenant_id)
        if not bool(getattr(settings, "youtube_auto_upload_after_pipeline", False)):
            return
        cid = (settings.youtube_client_id or "").strip()
        sec = (settings.youtube_client_secret or "").strip()
        rt = (settings.youtube_refresh_token or "").strip()
        if not cid or not sec or not rt:
            log.info("youtube_auto_upload_skipped_missing_oauth", agent_run_id=agent_run_id)
            return
        root = Path(settings.local_storage_root).resolve()
        tv = db.scalars(
            select(TimelineVersion)
            .where(TimelineVersion.project_id == run.project_id, TimelineVersion.tenant_id == run.tenant_id)
            .order_by(TimelineVersion.created_at.desc())
            .limit(1)
        ).first()
        if not tv:
            return
        final_p = root / "exports" / str(run.project_id) / str(tv.id) / "final_cut.mp4"
        if not path_is_readable_file(final_p):
            return
        tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
        if tj.get("youtube_auto_upload") is False:
            return
        try:
            assert_can_enqueue(db, settings, "youtube_upload", tenant_id=run.tenant_id)
        except Exception as exc:
            log.warning("youtube_auto_upload_enqueue_blocked", error=str(exc)[:300])
            return
        proj = db.get(Project, run.project_id)
        title = (proj.title if proj else "Video")[:500]
        job = Job(
            id=uuid.uuid4(),
            tenant_id=run.tenant_id,
            type="youtube_upload",
            status="queued",
            payload={
                "project_id": str(run.project_id),
                "tenant_id": run.tenant_id,
                "timeline_version_id": str(tv.id),
                "title": title,
                "description": f"Uploaded from Directely agent run {agent_run_id}",
                "privacy_status": str(tj.get("youtube_privacy_status") or "unlisted"),
            },
            project_id=run.project_id,
        )
        db.add(job)
        db.commit()
        job_id = job.id
    if job_id is not None:
        enqueue_job_task(run_phase5_job, job_id)
        log.info("youtube_upload_enqueued_after_agent_run", job_id=str(job_id), agent_run_id=agent_run_id)
