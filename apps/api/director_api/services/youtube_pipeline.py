"""Optional auto-upload to YouTube after a final export exists."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.db.models import TimelineVersion
from director_api.services.youtube_upload import (
    refresh_access_token,
    share_url_from_upload_response,
    upload_mp4_resumable,
)
from ffmpeg_pipelines.paths import path_is_readable_file

log = structlog.get_logger(__name__)


def _latest_timeline(db: Session, project_id: uuid.UUID, tenant_id: str) -> TimelineVersion | None:
    return db.scalars(
        select(TimelineVersion)
        .where(TimelineVersion.project_id == project_id, TimelineVersion.tenant_id == tenant_id)
        .order_by(TimelineVersion.created_at.desc())
        .limit(1)
    ).first()


def try_youtube_auto_upload(
    db: Session,
    settings: Any,
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    project_title: str,
    timeline_version_id: uuid.UUID | None = None,
) -> None:
    """Best-effort: if workspace has YouTube tokens + auto flag, upload ``final_cut.mp4``."""
    try:
        if not bool(getattr(settings, "youtube_auto_upload_after_export", False)):
            return
        cid = (getattr(settings, "youtube_client_id", None) or "").strip()
        csec = (getattr(settings, "youtube_client_secret", None) or "").strip()
        rtok = (getattr(settings, "youtube_refresh_token", None) or "").strip()
        if not cid or not csec or not rtok:
            return
        tv: TimelineVersion | None = None
        if timeline_version_id is not None:
            row = db.get(TimelineVersion, timeline_version_id)
            if row and row.tenant_id == tenant_id and row.project_id == project_id:
                tv = row
        if tv is None:
            tv = _latest_timeline(db, project_id, tenant_id)
        if not tv:
            return
        root = Path(getattr(settings, "local_storage_root", "") or "").resolve()
        vid = root / "exports" / str(project_id) / str(tv.id) / "final_cut.mp4"
        if not path_is_readable_file(vid):
            return
        access = refresh_access_token(refresh_token=rtok, client_id=cid, client_secret=csec)
        priv = str(getattr(settings, "youtube_default_privacy", None) or "unlisted").strip().lower()
        if priv not in ("public", "unlisted", "private"):
            priv = "unlisted"
        title = (project_title or "Export").strip()[:100] or "Export"
        desc = f"Exported from Directely — project {project_id}"
        resp = upload_mp4_resumable(
            access_token=access,
            file_path=vid,
            title=title,
            description=desc,
            privacy_status=priv,
        )
        url = share_url_from_upload_response(resp)
        tj = dict(tv.timeline_json or {}) if isinstance(tv.timeline_json, dict) else {}
        tj["youtube_last_upload"] = {
            "video_id": str(resp.get("id") or ""),
            "watch_url": url,
        }
        tv.timeline_json = tj
        flag_modified(tv, "timeline_json")
        db.add(tv)
        db.flush()
        log.info(
            "youtube_auto_upload_ok",
            project_id=str(project_id),
            timeline_version_id=str(tv.id),
            video_id=str(resp.get("id") or ""),
        )
    except Exception as exc:
        log.warning("youtube_auto_upload_failed", project_id=str(project_id), error=str(exc)[:500])
