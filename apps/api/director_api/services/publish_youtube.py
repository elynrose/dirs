"""Optional YouTube upload after final export."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.db.models import Project, TimelineVersion
from director_api.services.youtube_upload import (
    refresh_access_token,
    share_url_from_upload_response,
    upload_mp4_resumable,
)
from ffmpeg_pipelines.paths import path_is_readable_file

log = structlog.get_logger(__name__)


def resolve_publish_to_youtube(project: Project, pipeline_options: dict[str, Any] | None) -> bool:
    if isinstance(pipeline_options, dict) and pipeline_options.get("publish_to_youtube") is not None:
        return bool(pipeline_options.get("publish_to_youtube"))
    return bool(getattr(project, "publish_to_youtube", False))


def should_youtube_upload(settings: Any, *, publish_to_youtube: bool) -> bool:
    return bool(publish_to_youtube) or bool(getattr(settings, "youtube_auto_upload_after_export", False))


def youtube_upload_metadata(project: Project) -> tuple[str, str]:
    pack = project.publish_pack_json if isinstance(project.publish_pack_json, dict) else {}
    title = str(pack.get("youtube_title") or project.title or "Export").strip()[:100] or "Export"
    desc = str(pack.get("youtube_description") or project.topic or "").strip()[:5000]
    return title, desc


def _resolve_timeline(
    db: Session,
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    timeline_version_id: uuid.UUID | None,
) -> TimelineVersion | None:
    tv: TimelineVersion | None = None
    if timeline_version_id is not None:
        row = db.get(TimelineVersion, timeline_version_id)
        if row and row.tenant_id == tenant_id and row.project_id == project_id:
            tv = row
    if tv is None:
        tv = db.scalars(
            select(TimelineVersion)
            .where(TimelineVersion.project_id == project_id, TimelineVersion.tenant_id == tenant_id)
            .order_by(TimelineVersion.created_at.desc())
            .limit(1)
        ).first()
    return tv


def try_youtube_upload_after_export(
    db: Session,
    settings: Any,
    *,
    tenant_id: str,
    project: Project,
    publish_to_youtube: bool,
    timeline_version_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Best-effort upload of ``final_cut.mp4`` when per-project or workspace auto-upload is enabled."""
    if not should_youtube_upload(settings, publish_to_youtube=publish_to_youtube):
        return {"ok": False, "skipped_reason": "upload_not_requested"}

    cid = (getattr(settings, "youtube_client_id", None) or "").strip()
    csec = (getattr(settings, "youtube_client_secret", None) or "").strip()
    rtok = (getattr(settings, "youtube_refresh_token", None) or "").strip()
    if not cid or not csec or not rtok:
        return {"ok": False, "skipped_reason": "youtube_not_connected"}

    tv = _resolve_timeline(
        db,
        tenant_id=tenant_id,
        project_id=project.id,
        timeline_version_id=timeline_version_id,
    )
    if not tv:
        return {"ok": False, "skipped_reason": "timeline_missing"}

    root = Path(getattr(settings, "local_storage_root", "") or "").resolve()
    vid = root / "exports" / str(project.id) / str(tv.id) / "final_cut.mp4"
    if not path_is_readable_file(vid):
        return {"ok": False, "skipped_reason": "final_cut_missing"}

    try:
        access = refresh_access_token(refresh_token=rtok, client_id=cid, client_secret=csec)
        priv = str(getattr(settings, "youtube_default_privacy", None) or "unlisted").strip().lower()
        if priv not in ("public", "unlisted", "private"):
            priv = "unlisted"
        title, desc = youtube_upload_metadata(project)
        resp = upload_mp4_resumable(
            access_token=access,
            file_path=vid,
            title=title,
            description=desc,
            privacy_status=priv,
        )
        url = share_url_from_upload_response(resp)
        video_id = str(resp.get("id") or "")
        tj = dict(tv.timeline_json or {}) if isinstance(tv.timeline_json, dict) else {}
        tj["youtube_last_upload"] = {
            "video_id": video_id,
            "watch_url": url,
        }
        tv.timeline_json = tj
        flag_modified(tv, "timeline_json")
        db.add(tv)
        db.flush()
        log.info(
            "youtube_upload_after_export_ok",
            project_id=str(project.id),
            timeline_version_id=str(tv.id),
            video_id=video_id,
        )
        return {"ok": True, "video_id": video_id, "watch_url": url}
    except Exception as exc:
        err = str(exc)[:500]
        log.warning("youtube_upload_after_export_failed", project_id=str(project.id), error=err)
        return {"ok": False, "error": err}


def try_youtube_auto_upload(
    db: Session,
    settings: Any,
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    project_title: str,
    timeline_version_id: uuid.UUID | None = None,
    publish_to_youtube: bool = False,
    project: Project | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper used by final_cut jobs and manual export paths."""
    del project_title  # metadata comes from project row when available
    row = project if project is not None else db.get(Project, project_id)
    if not row or row.tenant_id != tenant_id:
        return {"ok": False, "skipped_reason": "project_missing"}
    return try_youtube_upload_after_export(
        db,
        settings,
        tenant_id=tenant_id,
        project=row,
        publish_to_youtube=publish_to_youtube,
        timeline_version_id=timeline_version_id,
    )
