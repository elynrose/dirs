"""Phase 5 — narration, timeline, compile (initial slice)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated
from uuid import UUID

import jsonschema
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.api.idempotency import (
    body_hash,
    idempotency_replay_or_conflict,
    require_idempotency_key,
    store_idempotency,
)
from director_api.api.routers.workflow_phase3 import _chapter_or_404, _scene_or_404, file_response_local_media
from director_api.services.scene_narration_mic_upload import save_scene_narration_from_microphone_upload
from director_api.services.tenant_entitlements import assert_subtitles_allowed
from director_api.api.schemas.phase5 import (
    ExportBundleBody,
    FinalCutBody,
    FineCutBody,
    MusicBedCreate,
    MusicBedOut,
    MusicBedPatch,
    RoughCutBody,
    TimelineVersionCreate,
    TimelineVersionOut,
    TimelineVersionPatch,
)
from director_api.config import Settings, get_settings
from director_api.db.models import Chapter, Job, MusicBed, NarrationTrack, Project, Scene, TimelineVersion
from director_api.db.session import get_db
from director_api.services.job_quota import assert_can_enqueue
from director_api.storage.filesystem import FilesystemStorage
from director_api.tasks.job_enqueue import enqueue_job_task
from director_api.tasks.worker_tasks import run_phase5_job
from director_api.validation.timeline_schema import validate_timeline_document
from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file

router = APIRouter(tags=["phase5"])

_MUSIC_UPLOAD_MAX_BYTES = 80 * 1024 * 1024


def _resolve_narration_vtt_path(*, storage_root: Path, project_id: uuid.UUID, chapter_id: uuid.UUID) -> Path | None:
    canonical = storage_root / "narrations" / str(project_id) / f"{chapter_id}.vtt"
    if path_is_readable_file(canonical):
        return canonical.resolve()
    under_assets = storage_root / "assets" / str(project_id) / "narrations" / f"{chapter_id}.vtt"
    if path_is_readable_file(under_assets):
        return under_assets.resolve()
    return None


def _resolve_narration_mp3_path(
    *,
    audio_url: str,
    storage_root: Path,
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
) -> Path | None:
    """Resolve stored audio_url; fall back to canonical paths if URL moved or legacy layout."""
    p = path_from_storage_url(audio_url, storage_root=storage_root)
    if p is not None and path_is_readable_file(p):
        return p
    canonical = storage_root / "narrations" / str(project_id) / f"{chapter_id}.mp3"
    if path_is_readable_file(canonical):
        return canonical.resolve()
    under_assets = storage_root / "assets" / str(project_id) / "narrations" / f"{chapter_id}.mp3"
    if path_is_readable_file(under_assets):
        return under_assets.resolve()
    return None


def _resolve_scene_narration_vtt_path(*, storage_root: Path, project_id: uuid.UUID, scene_id: uuid.UUID) -> Path | None:
    canonical = storage_root / "narrations" / str(project_id) / "scenes" / f"{scene_id}.vtt"
    if path_is_readable_file(canonical):
        return canonical.resolve()
    under_assets = storage_root / "assets" / str(project_id) / "narrations" / "scenes" / f"{scene_id}.vtt"
    if path_is_readable_file(under_assets):
        return under_assets.resolve()
    return None


def _resolve_scene_narration_mp3_path(
    *,
    audio_url: str,
    storage_root: Path,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
) -> Path | None:
    p = path_from_storage_url(audio_url, storage_root=storage_root)
    if p is not None and path_is_readable_file(p):
        return p
    canonical = storage_root / "narrations" / str(project_id) / "scenes" / f"{scene_id}.mp3"
    if path_is_readable_file(canonical):
        return canonical.resolve()
    under_assets = storage_root / "assets" / str(project_id) / "narrations" / "scenes" / f"{scene_id}.mp3"
    if path_is_readable_file(under_assets):
        return under_assets.resolve()
    return None


def _project_or_404(db: Session, settings: Settings, project_id: UUID) -> Project:
    p = db.get(Project, project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    return p


def _timeline_or_404(db: Session, settings: Settings, timeline_version_id: UUID) -> TimelineVersion:
    tv = db.get(TimelineVersion, timeline_version_id)
    if not tv or tv.tenant_id != settings.default_tenant_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found"},
        )
    return tv


def _music_bed_or_404(db: Session, settings: Settings, music_bed_id: UUID) -> MusicBed:
    mb = db.get(MusicBed, music_bed_id)
    if not mb or mb.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "music bed not found"})
    return mb


def _compiled_timeline_export_path(
    *,
    storage_root: Path,
    project_id: UUID,
    timeline_version_id: UUID,
) -> Path | None:
    """Best available export under ``exports/{project}/{timeline}/`` (final → fine → rough)."""
    base = storage_root / "exports" / str(project_id) / str(timeline_version_id)
    final_p = base / "final_cut.mp4"
    if path_is_readable_file(final_p):
        return final_p
    fine_p = base / "fine_cut.mp4"
    if path_is_readable_file(fine_p):
        return fine_p
    rough_p = base / "rough_cut.mp4"
    if path_is_readable_file(rough_p):
        return rough_p
    return None


@router.get("/chapters/{chapter_id}/narration")
def chapter_narration_meta(
    chapter_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    ch = _chapter_or_404(db, settings, chapter_id)
    _project_or_404(db, settings, ch.project_id)
    nt = db.scalar(
        select(NarrationTrack)
        .where(NarrationTrack.chapter_id == chapter_id, NarrationTrack.scene_id.is_(None))
        .order_by(NarrationTrack.created_at.desc())
    )
    root = Path(settings.local_storage_root).resolve()
    if not nt or not (nt.audio_url or "").strip():
        return {"data": {"has_audio": False, "has_subtitles": False, "chapter_id": str(chapter_id)}, "meta": meta}
    vtt_p = _resolve_narration_vtt_path(storage_root=root, project_id=ch.project_id, chapter_id=chapter_id)
    return {
        "data": {
            "has_audio": True,
            "has_subtitles": bool(vtt_p and path_is_readable_file(vtt_p)),
            "chapter_id": str(chapter_id),
            "track_id": str(nt.id),
            "duration_sec": nt.duration_sec,
            "created_at": nt.created_at.isoformat() if nt.created_at else None,
        },
        "meta": meta,
    }


@router.get("/chapters/{chapter_id}/narration/content")
def chapter_narration_content(
    chapter_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    ch = _chapter_or_404(db, settings, chapter_id)
    _project_or_404(db, settings, ch.project_id)
    nt = db.scalar(
        select(NarrationTrack)
        .where(NarrationTrack.chapter_id == chapter_id, NarrationTrack.scene_id.is_(None))
        .order_by(NarrationTrack.created_at.desc())
    )
    url = (nt.audio_url or "").strip() if nt else ""
    if not url:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "narration audio not generated yet"})
    root = Path(settings.local_storage_root).resolve()
    p = _resolve_narration_mp3_path(
        audio_url=url,
        storage_root=root,
        project_id=ch.project_id,
        chapter_id=chapter_id,
    )
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "narration file missing on disk"})
    return file_response_local_media(p)


@router.get("/chapters/{chapter_id}/narration/subtitles.vtt")
def chapter_narration_subtitles_vtt(
    chapter_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    """Kokoro-aligned WebVTT when present (same chapter as ``narration/content``)."""
    ch = _chapter_or_404(db, settings, chapter_id)
    _project_or_404(db, settings, ch.project_id)
    root = Path(settings.local_storage_root).resolve()
    vtt_p = _resolve_narration_vtt_path(storage_root=root, project_id=ch.project_id, chapter_id=chapter_id)
    if vtt_p is None or not path_is_readable_file(vtt_p):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "narration subtitles not available for this chapter"},
        )
    return FileResponse(
        path=vtt_p,
        media_type="text/vtt; charset=utf-8",
        filename=vtt_p.name,
        content_disposition_type="inline",
    )


@router.post("/chapters/{chapter_id}/narration/generate")
def narration_generate(
    chapter_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    ch = _chapter_or_404(db, settings, chapter_id)
    _project_or_404(db, settings, ch.project_id)

    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/chapters/{chapter_id}/narration/generate"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "narration_generate")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="narration_generate",
        status="queued",
        payload={
            "chapter_id": str(chapter_id),
            "tenant_id": settings.default_tenant_id,
        },
        project_id=ch.project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase5_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.get("/projects/{project_id}/timeline-versions")
def list_timeline_versions(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    rows = list(
        db.scalars(
            select(TimelineVersion)
            .where(
                TimelineVersion.project_id == project_id,
                TimelineVersion.tenant_id == settings.default_tenant_id,
            )
            .order_by(TimelineVersion.created_at.desc())
        ).all()
    )
    return {
        "data": [TimelineVersionOut.model_validate(r).model_dump(mode="json") for r in rows],
        "meta": meta,
    }


@router.post("/projects/{project_id}/timeline-versions")
def create_timeline_version(
    project_id: UUID,
    body: TimelineVersionCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    try:
        validate_timeline_document(body.timeline_json)
    except jsonschema.ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VALIDATION_ERROR",
                "message": e.message,
                "path": [str(x) for x in e.path],
            },
        ) from e
    tv = TimelineVersion(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        project_id=project_id,
        version_name=body.version_name[:128],
        timeline_json=body.timeline_json,
        render_status="draft",
        output_url=None,
    )
    db.add(tv)
    db.commit()
    db.refresh(tv)
    return {"data": TimelineVersionOut.model_validate(tv).model_dump(mode="json"), "meta": meta}


@router.get("/projects/{project_id}/timeline-versions/{timeline_version_id}/compiled-video")
def get_project_timeline_compiled_video(
    project_id: UUID,
    timeline_version_id: UUID,
    download: Annotated[
        bool,
        Query(description="If true, Content-Disposition is attachment (download) instead of inline."),
    ] = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    """
    Stream the on-disk compiled video for this timeline (``final_cut.mp4`` if present, else ``fine_cut`` / ``rough_cut``).
    Same resolution order as the export-bundle worker. Use for in-app preview and downloads.
    """
    _project_or_404(db, settings, project_id)
    tv = db.get(TimelineVersion, timeline_version_id)
    if not tv or tv.tenant_id != settings.default_tenant_id or tv.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found for this project"},
        )
    root = Path(settings.local_storage_root).resolve()
    p = _compiled_timeline_export_path(
        storage_root=root,
        project_id=project_id,
        timeline_version_id=timeline_version_id,
    )
    if p is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "COMPILED_VIDEO_NOT_FOUND",
                "message": "No compiled video on disk for this timeline — run rough cut, final cut, or export first.",
            },
        )
    return file_response_local_media(
        p,
        content_disposition_type="attachment" if download else None,
    )


@router.head("/projects/{project_id}/timeline-versions/{timeline_version_id}/compiled-video")
def head_project_timeline_compiled_video(
    project_id: UUID,
    timeline_version_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    _project_or_404(db, settings, project_id)
    tv = db.get(TimelineVersion, timeline_version_id)
    if not tv or tv.tenant_id != settings.default_tenant_id or tv.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found for this project"},
        )
    root = Path(settings.local_storage_root).resolve()
    p = _compiled_timeline_export_path(
        storage_root=root,
        project_id=project_id,
        timeline_version_id=timeline_version_id,
    )
    if p is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "COMPILED_VIDEO_NOT_FOUND",
                "message": "No compiled video on disk for this timeline — run rough cut, final cut, or export first.",
            },
        )
    return Response(status_code=200)


@router.get("/timeline-versions/{timeline_version_id}")
def get_timeline_version(
    timeline_version_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    tv = _timeline_or_404(db, settings, timeline_version_id)
    _project_or_404(db, settings, tv.project_id)
    return {"data": TimelineVersionOut.model_validate(tv).model_dump(mode="json"), "meta": meta}


@router.patch("/timeline-versions/{timeline_version_id}")
def patch_timeline_version(
    timeline_version_id: UUID,
    body: TimelineVersionPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    tv = _timeline_or_404(db, settings, timeline_version_id)
    _project_or_404(db, settings, tv.project_id)
    if body.version_name is not None:
        tv.version_name = body.version_name[:128]
    if body.timeline_json is not None:
        try:
            validate_timeline_document(body.timeline_json)
        except jsonschema.ValidationError as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "VALIDATION_ERROR",
                    "message": e.message,
                    "path": [str(x) for x in e.path],
                },
            ) from e
        tv.timeline_json = body.timeline_json
    if body.render_status is not None:
        tv.render_status = body.render_status[:32]
    if body.output_url is not None:
        tv.output_url = body.output_url
    db.commit()
    db.refresh(tv)
    return {"data": TimelineVersionOut.model_validate(tv).model_dump(mode="json"), "meta": meta}


@router.get("/projects/{project_id}/music-beds")
def list_music_beds(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    tenant_ok = MusicBed.tenant_id == settings.default_tenant_id
    # Legacy / self-hosted (auth off): single operator — show every bed in the tenant in every project's picker.
    # SaaS / signed in: this project's beds plus the current user's library (uploads may use project_id NULL).
    if not settings.director_auth_enabled:
        scope = True
    elif auth.user_id:
        try:
            uid = int(str(auth.user_id).strip())
        except (ValueError, TypeError):
            uid = None
        if uid is not None:
            scope = or_(MusicBed.project_id == project_id, MusicBed.uploaded_by_user_id == uid)
        else:
            scope = MusicBed.project_id == project_id
    else:
        scope = MusicBed.project_id == project_id
    rows = list(
        db.scalars(
            select(MusicBed).where(tenant_ok, scope).order_by(MusicBed.created_at.desc())
        ).all()
    )
    return {
        "data": [MusicBedOut.model_validate(r).model_dump(mode="json") for r in rows],
        "meta": meta,
    }


@router.post("/projects/{project_id}/music-beds")
def create_music_bed(
    project_id: UUID,
    body: MusicBedCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    starter_uid: int | None = None
    if auth.user_id:
        try:
            starter_uid = int(str(auth.user_id).strip())
        except (ValueError, TypeError):
            starter_uid = None
    mb = MusicBed(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        project_id=None if starter_uid is not None else project_id,
        uploaded_by_user_id=starter_uid,
        title=body.title[:500],
        storage_url=body.storage_url,
        license_or_source_ref=body.license_or_source_ref,
        mix_config_json=body.mix_config_json,
    )
    db.add(mb)
    db.commit()
    db.refresh(mb)
    return {"data": MusicBedOut.model_validate(mb).model_dump(mode="json"), "meta": meta}


@router.patch("/music-beds/{music_bed_id}")
def patch_music_bed(
    music_bed_id: UUID,
    body: MusicBedPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    mb = _music_bed_or_404(db, settings, music_bed_id)
    if mb.project_id is not None:
        _project_or_404(db, settings, mb.project_id)
    elif mb.uploaded_by_user_id is not None:
        if not auth.user_id or str(mb.uploaded_by_user_id) != str(auth.user_id):
            raise HTTPException(
                status_code=403,
                detail={"code": "FORBIDDEN", "message": "not allowed to edit this music bed"},
            )
    if body.title is not None:
        mb.title = body.title[:500]
    if body.storage_url is not None:
        mb.storage_url = body.storage_url
    if body.license_or_source_ref is not None:
        mb.license_or_source_ref = body.license_or_source_ref
    if body.mix_config_json is not None:
        mb.mix_config_json = body.mix_config_json
    db.commit()
    db.refresh(mb)
    return {"data": MusicBedOut.model_validate(mb).model_dump(mode="json"), "meta": meta}


@router.post("/projects/{project_id}/music-beds/upload")
async def upload_music_bed_file(
    project_id: UUID,
    file: UploadFile = File(...),
    title: str = Form("Uploaded music"),
    license_or_source_ref: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    lic = (license_or_source_ref or "").strip()
    if len(lic) < 2:
        raise HTTPException(
            status_code=422,
            detail={"code": "LICENSE_REQUIRED", "message": "license_or_source_ref is required for compliance"},
        )
    raw_parts: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MUSIC_UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"code": "TOO_LARGE", "message": "upload exceeds 80 MB"},
            )
        raw_parts.append(chunk)
    raw = b"".join(raw_parts)
    if len(raw) < 64:
        raise HTTPException(status_code=422, detail={"code": "EMPTY", "message": "uploaded file too small"})
    ext = Path(file.filename or "audio.mp3").suffix.lower()
    if ext not in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm"):
        ext = ".mp3"
    storage = FilesystemStorage(settings.local_storage_root)
    starter_uid: int | None = None
    if auth.user_id:
        try:
            starter_uid = int(str(auth.user_id).strip())
        except (ValueError, TypeError):
            starter_uid = None
    if starter_uid is not None:
        key = f"music_beds/user/{starter_uid}/{uuid.uuid4().hex}{ext}"
    else:
        key = f"music_beds/{project_id}/{uuid.uuid4().hex}{ext}"
    ct = file.content_type or ("audio/mpeg" if ext == ".mp3" else "application/octet-stream")
    url = storage.put_bytes(key, raw, content_type=ct)
    mb = MusicBed(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        project_id=project_id,
        uploaded_by_user_id=starter_uid,
        title=(title or "Uploaded music")[:500],
        storage_url=url,
        license_or_source_ref=lic,
        mix_config_json=None,
    )
    db.add(mb)
    db.commit()
    db.refresh(mb)
    return {"data": MusicBedOut.model_validate(mb).model_dump(mode="json"), "meta": meta}


@router.post("/scenes/{scene_id}/narration/generate")
def scene_narration_generate(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    sc = _scene_or_404(db, settings, scene_id)
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    _project_or_404(db, settings, ch.project_id)

    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/scenes/{scene_id}/narration/generate"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "narration_generate_scene")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="narration_generate_scene",
        status="queued",
        payload={
            "scene_id": str(scene_id),
            "tenant_id": settings.default_tenant_id,
        },
        project_id=ch.project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase5_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


_SCENE_VO_UPLOAD_MAX_BYTES = 40 * 1024 * 1024


@router.post("/scenes/{scene_id}/narration/upload")
async def scene_narration_upload_microphone(
    scene_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Save recorded or uploaded audio as this scene's VO (``NarrationTrack``), replacing any existing track.

    Browser ``MediaRecorder`` typically sends ``audio/webm``; the server transcodes to MP3. Max length ~600s.
    """
    sc = _scene_or_404(db, settings, scene_id)
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    p = _project_or_404(db, settings, ch.project_id)

    raw_parts: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _SCENE_VO_UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"code": "TOO_LARGE", "message": f"upload exceeds {_SCENE_VO_UPLOAD_MAX_BYTES // (1024 * 1024)} MB"},
            )
        raw_parts.append(chunk)
    raw = b"".join(raw_parts)

    try:
        out = save_scene_narration_from_microphone_upload(
            db,
            scene=sc,
            project_id=p.id,
            chapter_id=ch.id,
            tenant_id=settings.default_tenant_id,
            raw_bytes=raw,
            original_filename=file.filename or "recording.webm",
            settings=settings,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "NARRATION_UPLOAD_INVALID", "message": str(e)}) from e

    return {"data": out, "meta": meta}


@router.post("/projects/{project_id}/narration/generate-all-scenes")
def project_narration_generate_all_scenes(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
):
    """Queue a ``narration_generate_scene`` job for every scene that has
    ``narration_text`` but no scene-level ``NarrationTrack`` with audio yet."""
    p = db.get(Project, project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})

    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )

    jobs_created: list[dict[str, str]] = []
    skipped = 0
    for sc in scenes:
        if len((sc.narration_text or "").strip()) < 2:
            skipped += 1
            continue
        has_track = db.scalar(
            select(NarrationTrack.id)
            .where(
                NarrationTrack.scene_id == sc.id,
                NarrationTrack.audio_url.isnot(None),
            )
            .limit(1)
        )
        if has_track:
            skipped += 1
            continue
        job = Job(
            id=uuid.uuid4(),
            tenant_id=settings.default_tenant_id,
            type="narration_generate_scene",
            status="queued",
            payload={
                "scene_id": str(sc.id),
                "tenant_id": settings.default_tenant_id,
            },
            project_id=project_id,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        enqueue_job_task(run_phase5_job, job.id)
        jobs_created.append({"job_id": str(job.id), "scene_id": str(sc.id)})

    return {
        "data": {
            "jobs_queued": len(jobs_created),
            "scenes_skipped": skipped,
            "jobs": jobs_created,
        },
        "meta": meta,
    }


@router.get("/scenes/{scene_id}/narration")
def scene_narration_meta(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    sc = _scene_or_404(db, settings, scene_id)
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    _project_or_404(db, settings, ch.project_id)
    nt = db.scalar(
        select(NarrationTrack)
        .where(
            NarrationTrack.project_id == ch.project_id,
            NarrationTrack.scene_id == scene_id,
            NarrationTrack.audio_url.isnot(None),
        )
        .order_by(NarrationTrack.created_at.desc())
    )
    root = Path(settings.local_storage_root).resolve()
    if not nt or not (nt.audio_url or "").strip():
        return {
            "data": {
                "has_audio": False,
                "has_subtitles": False,
                "scene_id": str(scene_id),
                "chapter_id": str(ch.id),
            },
            "meta": meta,
        }
    vtt_p = _resolve_scene_narration_vtt_path(storage_root=root, project_id=ch.project_id, scene_id=scene_id)
    return {
        "data": {
            "has_audio": True,
            "has_subtitles": bool(vtt_p and path_is_readable_file(vtt_p)),
            "scene_id": str(scene_id),
            "chapter_id": str(ch.id),
            "track_id": str(nt.id),
            "duration_sec": nt.duration_sec,
            "created_at": nt.created_at.isoformat() if nt.created_at else None,
        },
        "meta": meta,
    }


@router.get("/scenes/{scene_id}/narration/content")
def scene_narration_content(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    sc = _scene_or_404(db, settings, scene_id)
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    _project_or_404(db, settings, ch.project_id)
    nt = db.scalar(
        select(NarrationTrack)
        .where(
            NarrationTrack.project_id == ch.project_id,
            NarrationTrack.scene_id == scene_id,
            NarrationTrack.audio_url.isnot(None),
        )
        .order_by(NarrationTrack.created_at.desc())
    )
    url = (nt.audio_url or "").strip() if nt else ""
    if not url:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "scene narration audio not generated yet"},
        )
    root = Path(settings.local_storage_root).resolve()
    p = _resolve_scene_narration_mp3_path(
        audio_url=url,
        storage_root=root,
        project_id=ch.project_id,
        scene_id=scene_id,
    )
    if p is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "scene narration file missing on disk"},
        )
    return file_response_local_media(p)


@router.get("/scenes/{scene_id}/narration/subtitles.vtt")
def scene_narration_subtitles_vtt(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    sc = _scene_or_404(db, settings, scene_id)
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    _project_or_404(db, settings, ch.project_id)
    root = Path(settings.local_storage_root).resolve()
    vtt_p = _resolve_scene_narration_vtt_path(storage_root=root, project_id=ch.project_id, scene_id=scene_id)
    if vtt_p is None or not path_is_readable_file(vtt_p):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "scene narration subtitles not available"},
        )
    return FileResponse(
        path=vtt_p,
        media_type="text/vtt; charset=utf-8",
        filename=vtt_p.name,
        content_disposition_type="inline",
    )


@router.post("/projects/{project_id}/rough-cut")
def rough_cut(
    project_id: UUID,
    body: RoughCutBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _project_or_404(db, settings, project_id)
    tv = db.get(TimelineVersion, body.timeline_version_id)
    if (
        not tv
        or tv.tenant_id != settings.default_tenant_id
        or tv.project_id != project_id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found for this project"},
        )

    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/rough-cut"
    h = body_hash(body.model_dump(mode="json"))
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "rough_cut")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="rough_cut",
        status="queued",
        payload={
            "timeline_version_id": str(body.timeline_version_id),
            "project_id": str(project_id),
            "tenant_id": settings.default_tenant_id,
            "allow_unapproved_media": body.allow_unapproved_media,
        },
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase5_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.post("/projects/{project_id}/fine-cut")
def fine_cut(
    project_id: UUID,
    body: FineCutBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Fine cut: burn ``timeline_json.overlays`` (titles, lower thirds, map placeholders) onto rough_cut → fine_cut."""
    _project_or_404(db, settings, project_id)
    tv = db.get(TimelineVersion, body.timeline_version_id)
    if (
        not tv
        or tv.tenant_id != settings.default_tenant_id
        or tv.project_id != project_id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found for this project"},
        )

    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/fine-cut"
    h = body_hash(body.model_dump(mode="json"))
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "fine_cut")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="fine_cut",
        status="queued",
        payload={
            "timeline_version_id": str(body.timeline_version_id),
            "project_id": str(project_id),
            "tenant_id": settings.default_tenant_id,
            "allow_unapproved_media": body.allow_unapproved_media,
        },
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase5_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.post("/projects/{project_id}/subtitles/generate")
def subtitles_generate(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Queue WebVTT generation from scene ``narration_text`` (story order); chapter scripts if no scene text."""
    _project_or_404(db, settings, project_id)
    assert_subtitles_allowed(
        db=db, tenant_id=settings.default_tenant_id, auth_enabled=bool(get_settings().director_auth_enabled)
    )
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/subtitles/generate"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay
    assert_can_enqueue(db, settings, "subtitles_generate")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="subtitles_generate",
        status="queued",
        payload={
            "project_id": str(project_id),
            "tenant_id": settings.default_tenant_id,
        },
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase5_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.post("/projects/{project_id}/final-cut")
def final_cut(
    project_id: UUID,
    body: FinalCutBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _project_or_404(db, settings, project_id)
    tv = db.get(TimelineVersion, body.timeline_version_id)
    if not tv or tv.tenant_id != settings.default_tenant_id or tv.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found for this project"},
        )
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/final-cut"
    h = body_hash(body.model_dump(mode="json"))
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay
    assert_can_enqueue(db, settings, "final_cut")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="final_cut",
        status="queued",
        payload={
            "timeline_version_id": str(body.timeline_version_id),
            "project_id": str(project_id),
            "tenant_id": settings.default_tenant_id,
            "allow_unapproved_media": body.allow_unapproved_media,
            "burn_subtitles_into_video": body.burn_subtitles_into_video,
        },
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase5_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.post("/projects/{project_id}/export")
def export_bundle(
    project_id: UUID,
    body: ExportBundleBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _project_or_404(db, settings, project_id)
    tv = db.get(TimelineVersion, body.timeline_version_id)
    if not tv or tv.tenant_id != settings.default_tenant_id or tv.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found for this project"},
        )
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/export"
    h = body_hash(body.model_dump(mode="json"))
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay
    assert_can_enqueue(db, settings, "export")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="export",
        status="queued",
        payload={
            "timeline_version_id": str(body.timeline_version_id),
            "project_id": str(project_id),
            "tenant_id": settings.default_tenant_id,
            "include_subtitles": body.include_subtitles,
        },
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase5_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)
