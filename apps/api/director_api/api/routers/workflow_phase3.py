"""Phase 3 — scenes and image assets."""

from __future__ import annotations

from collections import Counter
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import structlog
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.idempotency import (
    body_hash,
    idempotency_replay_or_conflict,
    require_idempotency_key,
    store_idempotency,
)
from director_api.services.job_quota import assert_can_enqueue
from director_api.api.schemas.phase3 import (
    AssetOut,
    AssetRejectBody,
    PromptEnhanceImageBody,
    PromptEnhanceVoBody,
    SceneAssetSequenceBody,
    SceneImageGenBody,
    SceneOut,
    ScenePatch,
    ScenesGenerateBody,
    SceneVideoGenBody,
)
from director_api.config import Settings
from director_api.db.models import Asset, Chapter, Job, Project, Scene
from director_api.db.session import get_db
from director_api.services import phase3 as phase3_svc
from director_api.services.prompt_enhance import enhance_image_retry_prompt, enhance_scene_vo_script
from director_api.services.scene_clip_upload import (
    AMBIGUOUS_EXTS,
    MAX_UPLOAD_BYTES,
    assert_clip_duration_within_limit,
    classify_from_filename_and_hint,
    normalized_extension,
    refine_ambiguous_kind,
    validate_explicit_clip_kind,
)
from director_api.storage.filesystem import FilesystemStorage
from director_api.tasks.job_enqueue import enqueue_job_task, enqueue_run_phase3_job
from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file

router = APIRouter(tags=["phase3"])
log = structlog.get_logger(__name__)


def _path_within_storage(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_asset_local_path(a: Asset, *, storage_root: Path) -> Path | None:
    """Resolve on-disk file for an asset; tolerate legacy file:// forms via storage_key / canonical layout."""
    root = storage_root.resolve()
    for url in (a.preview_url, a.storage_url):
        if not url or not str(url).strip():
            continue
        p = path_from_storage_url(url, storage_root=root)
        if p is not None and path_is_readable_file(p) and _path_within_storage(p, root):
            return p
    pj = a.params_json if isinstance(a.params_json, dict) else {}
    sk = pj.get("storage_key")
    if isinstance(sk, str) and sk.strip():
        safe = sk.strip().lstrip("/").replace("..", "")
        p2 = (root / safe).resolve()
        if path_is_readable_file(p2) and _path_within_storage(p2, root):
            log.info("asset_content_via_storage_key", asset_id=str(a.id))
            return p2
    base = root / "assets" / str(a.project_id) / str(a.scene_id)
    for ext in (
        "png",
        "jpg",
        "jpeg",
        "webp",
        "gif",
        "mp4",
        "webm",
        "mov",
        "m4v",
        "mkv",
        "avi",
        "mp3",
        "wav",
        "m4a",
        "aac",
        "flac",
        "ogg",
        "opus",
    ):
        cand = (base / f"{a.id}.{ext}").resolve()
        if path_is_readable_file(cand) and _path_within_storage(cand, root):
            log.info("asset_content_via_canonical_guess", asset_id=str(a.id), ext=ext)
            return cand
    return None


def _chapter_or_404(db: Session, settings: Settings, chapter_id: UUID) -> Chapter:
    ch = db.get(Chapter, chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    p = db.get(Project, ch.project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    return ch


def _scene_or_404(db: Session, settings: Settings, scene_id: UUID) -> Scene:
    sc = db.get(Scene, scene_id)
    if not sc:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "scene not found"})
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "scene not found"})
    p = db.get(Project, ch.project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "scene not found"})
    return sc


def _asset_or_404(db: Session, settings: Settings, asset_id: UUID) -> Asset:
    a = db.get(Asset, asset_id)
    if not a or a.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "asset not found"})
    return a


def file_response_local_media(
    path: Path,
    *,
    content_disposition_type: str | None = None,
) -> FileResponse:
    """Serve local media; default inline for image/video/audio so players and range requests work."""
    ctype, _ = mimetypes.guess_type(str(path))
    media_type = ctype or "application/octet-stream"
    top = (media_type.split("/", 1)[0] or "").lower()
    if content_disposition_type in ("inline", "attachment"):
        disposition = content_disposition_type
    else:
        disposition = "inline" if top in ("image", "video", "audio") else "attachment"
    return FileResponse(
        path=path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type=disposition,
    )


def _enqueue_scene_image_job(
    db: Session,
    settings: Settings,
    *,
    scene_id: UUID,
    route: str,
    body: SceneImageGenBody,
    idempotency_key: str | None,
    meta: dict,
) -> JSONResponse:
    sc = _scene_or_404(db, settings, scene_id)
    ch = db.get(Chapter, sc.chapter_id)
    assert ch is not None
    key = require_idempotency_key(idempotency_key)
    payload_body = body.model_dump(mode="json", exclude_none=True)
    h = body_hash(payload_body)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "scene_generate_image")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="scene_generate_image",
        status="queued",
        payload={
            "scene_id": str(scene_id),
            "tenant_id": settings.default_tenant_id,
            "generation_tier": body.generation_tier,
            "image_prompt_override": body.image_prompt_override,
            "image_provider": body.image_provider,
            "fal_image_model": body.fal_image_model,
        },
        project_id=ch.project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    log.info(
        "scene_generate_image_enqueued",
        scene_id=str(scene_id),
        job_id=str(job.id),
        image_provider=body.image_provider,
    )
    enqueue_run_phase3_job(job.id)
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


@router.post("/chapters/{chapter_id}/scenes/generate")
def scenes_generate(
    chapter_id: UUID,
    body: ScenesGenerateBody = Body(default_factory=ScenesGenerateBody),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    ch = _chapter_or_404(db, settings, chapter_id)
    if not phase3_svc.chapter_eligible_for_scene_planning(ch):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCRIPT_REQUIRED",
                "message": (
                    "chapter needs script_text (or a substantive chapter summary) before scene planning — "
                    "at least 12 characters in one or the other"
                ),
            },
        )
    n_existing = int(
        db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == chapter_id)) or 0
    )
    if n_existing > 0 and not body.replace_existing_scenes:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCENES_ALREADY_PLANNED",
                "message": (
                    f"This chapter already has {n_existing} scene(s). "
                    "Use POST /v1/chapters/<id>/scenes/extend to append one beat without removing them. "
                    "To wipe and replan from the script, post this endpoint with JSON "
                    '{"replace_existing_scenes": true}.'
                ),
            },
        )
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/chapters/{chapter_id}/scenes/generate"
    payload_body = body.model_dump(mode="json")
    h = body_hash(payload_body)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "scene_generate")
    log.info(
        "scene_generate_enqueued",
        chapter_id=str(chapter_id),
        replace_existing_scenes=bool(body.replace_existing_scenes),
        existing_scene_count=n_existing,
    )
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="scene_generate",
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
    enqueue_run_phase3_job(job.id)
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


@router.post("/chapters/{chapter_id}/scenes/extend")
def scenes_extend(
    chapter_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Enqueue a job that appends one new scene after existing plans (LLM + chapter context)."""
    ch = _chapter_or_404(db, settings, chapter_id)
    if not phase3_svc.chapter_eligible_for_scene_extend(ch):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCRIPT_REQUIRED",
                "message": (
                    "Add chapter script_text or a substantive summary (12+ chars), **or** ensure existing "
                    "scenes have enough narration/purpose text to continue from — then try Extend again."
                ),
            },
        )
    n_scenes = int(
        db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == chapter_id)) or 0
    )
    if n_scenes < 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_SCENES",
                "message": "Plan scenes for this chapter first, then use Extend scene to add another beat.",
            },
        )
    if n_scenes >= 48:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCENE_LIMIT",
                "message": "This chapter already has the maximum number of scenes (48).",
            },
        )
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/chapters/{chapter_id}/scenes/extend"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "scene_extend")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="scene_extend",
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
    log.info("scene_extend_enqueued", chapter_id=str(chapter_id), job_id=str(job.id))
    enqueue_run_phase3_job(job.id)
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


@router.get("/chapters/{chapter_id}/scenes")
def list_scenes(
    chapter_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _chapter_or_404(db, settings, chapter_id)
    rows = db.scalars(select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.order_index)).all()
    out = []
    for sc in rows:
        n_assets = db.scalar(select(func.count()).select_from(Asset).where(Asset.scene_id == sc.id))
        out.append(
            SceneOut.model_validate(sc)
            .model_copy(update={"asset_count": int(n_assets or 0)})
            .model_dump(mode="json")
        )
    return {"data": {"scenes": out}, "meta": meta}


@router.get("/chapters/{chapter_id}/phase3-summary")
def phase3_chapter_summary(
    chapter_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Aggregate scene/asset counts for manual P3-M01 / P3-X01 checks."""
    _chapter_or_404(db, settings, chapter_id)
    scenes = db.scalars(select(Scene).where(Scene.chapter_id == chapter_id)).all()
    scene_ids = [s.id for s in scenes]
    if not scene_ids:
        return {
            "data": {
                "chapter_id": str(chapter_id),
                "scene_count": 0,
                "assets_total": 0,
                "assets_by_status": {},
                "approved_image_count": 0,
                "approved_video_count": 0,
                "failed_asset_count": 0,
                "linked_video_asset_count": 0,
                "p3_exit_image_ok": False,
                "p3_exit_video_ok": False,
                "p3_exit_any_approved_media": False,
                "notes": "No scenes — run POST /v1/chapters/{id}/scenes/generate first.",
            },
            "meta": meta,
        }

    assets = db.scalars(select(Asset).where(Asset.scene_id.in_(scene_ids))).all()
    by_status = Counter(a.status for a in assets)
    approved_img = sum(1 for a in assets if a.asset_type == "image" and a.approved_at is not None)
    approved_vid = sum(1 for a in assets if a.asset_type == "video" and a.approved_at is not None)
    failed = sum(1 for a in assets if a.status == "failed")
    linked_vid = sum(1 for a in assets if a.asset_type == "video")

    return {
        "data": {
            "chapter_id": str(chapter_id),
            "scene_count": len(scenes),
            "assets_total": len(assets),
            "assets_by_status": dict(by_status),
            "approved_image_count": approved_img,
            "approved_video_count": approved_vid,
            "failed_asset_count": failed,
            "linked_video_asset_count": linked_vid,
            "p3_exit_image_ok": approved_img >= 1,
            "p3_exit_video_ok": approved_vid >= 1,
            "p3_exit_any_approved_media": approved_img >= 1 or approved_vid >= 1,
            "notes": (
                "P3 exit: at least one approved image or video asset on this chapter. "
                "Scene video uses local FFmpeg still→MP4 from the latest succeeded scene image."
            ),
        },
        "meta": meta,
    }


@router.get("/scenes/{scene_id}")
def get_scene(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    sc = _scene_or_404(db, settings, scene_id)
    n_assets = db.scalar(select(func.count()).select_from(Asset).where(Asset.scene_id == sc.id))
    return {
        "data": SceneOut.model_validate(sc)
        .model_copy(update={"asset_count": int(n_assets or 0)})
        .model_dump(mode="json"),
        "meta": meta,
    }


@router.patch("/scenes/{scene_id}")
def patch_scene(
    scene_id: UUID,
    body: ScenePatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    sc = _scene_or_404(db, settings, scene_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        return {"data": SceneOut.model_validate(sc).model_dump(mode="json"), "meta": meta}
    for k, v in data.items():
        setattr(sc, k, v)
    db.commit()
    db.refresh(sc)
    return {"data": SceneOut.model_validate(sc).model_dump(mode="json"), "meta": meta}


@router.post("/scenes/{scene_id}/prompt-enhance-image")
def scene_prompt_enhance_image(
    scene_id: UUID,
    body: PromptEnhanceImageBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Rewrite image retry prompt using previous scene + character bible context."""
    _scene_or_404(db, settings, scene_id)
    text, err = enhance_image_retry_prompt(
        db,
        settings,
        scene_id=scene_id,
        current_prompt=body.current_prompt,
    )
    if err == "scene not found" or err == "chapter not found" or err == "project not found":
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": err})
    if err and "not configured" in err.lower():
        raise HTTPException(
            status_code=503,
            detail={"code": "TEXT_GEN_UNAVAILABLE", "message": err},
        )
    if not text:
        raise HTTPException(
            status_code=502,
            detail={"code": "PROMPT_ENHANCE_FAILED", "message": err or "empty result"},
        )
    return {"data": {"text": text}, "meta": meta}


@router.post("/scenes/{scene_id}/prompt-enhance-vo")
def scene_prompt_enhance_vo(
    scene_id: UUID,
    body: PromptEnhanceVoBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Rewrite scene narration to match narration style (project or override)."""
    _scene_or_404(db, settings, scene_id)
    text, err = enhance_scene_vo_script(
        db,
        settings,
        scene_id=scene_id,
        current_script=body.current_script,
        narration_style_prompt_override=body.narration_style_prompt,
    )
    if err == "scene not found" or err == "chapter not found" or err == "project not found":
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": err})
    if err and "not configured" in err.lower():
        raise HTTPException(
            status_code=503,
            detail={"code": "TEXT_GEN_UNAVAILABLE", "message": err},
        )
    if err and "narration style could not be resolved" in err.lower():
        raise HTTPException(status_code=400, detail={"code": "NARRATION_STYLE_MISSING", "message": err})
    if not text:
        raise HTTPException(
            status_code=502,
            detail={"code": "PROMPT_ENHANCE_FAILED", "message": err or "empty result"},
        )
    return {"data": {"text": text}, "meta": meta}


@router.post("/scenes/{scene_id}/generate-image")
def scene_generate_image(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    body: SceneImageGenBody | None = Body(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    route = f"POST /v1/scenes/{scene_id}/generate-image"
    return _enqueue_scene_image_job(
        db,
        settings,
        scene_id=scene_id,
        route=route,
        body=body or SceneImageGenBody(),
        idempotency_key=idempotency_key,
        meta=meta,
    )


@router.post("/scenes/{scene_id}/retry")
def scene_retry_image(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    body: SceneImageGenBody | None = Body(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """New image attempt; prior assets remain in history (P3-D05)."""
    route = f"POST /v1/scenes/{scene_id}/retry"
    return _enqueue_scene_image_job(
        db,
        settings,
        scene_id=scene_id,
        route=route,
        body=body or SceneImageGenBody(),
        idempotency_key=idempotency_key,
        meta=meta,
    )


@router.post("/scenes/{scene_id}/generate-video")
def scene_generate_video(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    body: SceneVideoGenBody | None = Body(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    sc = _scene_or_404(db, settings, scene_id)
    ch = db.get(Chapter, sc.chapter_id)
    assert ch is not None
    eff = body or SceneVideoGenBody()
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/scenes/{scene_id}/generate-video"
    payload_body = eff.model_dump(mode="json", exclude_none=True)
    h = body_hash(payload_body)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "scene_generate_video")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="scene_generate_video",
        status="queued",
        payload={
            "scene_id": str(scene_id),
            "tenant_id": settings.default_tenant_id,
            "generation_tier": eff.generation_tier,
            "notes": eff.notes,
            "video_provider": eff.video_provider,
            "fal_video_model": eff.fal_video_model,
            "video_prompt_override": eff.video_prompt_override,
        },
        project_id=ch.project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    log.info(
        "scene_generate_video_enqueued",
        scene_id=str(scene_id),
        job_id=str(job.id),
        video_provider=eff.video_provider,
    )
    enqueue_run_phase3_job(job.id)
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


@router.post("/assets/{asset_id}/approve")
def approve_asset(
    asset_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    a = _asset_or_404(db, settings, asset_id)
    a.approved_at = datetime.now(timezone.utc)
    root = Path(settings.local_storage_root).resolve()
    local_path = _resolve_asset_local_path(a, storage_root=root)
    at = str(a.asset_type or "").lower()
    # Export / gallery treat "succeeded" as the usable state. Promote image/video/audio to succeeded when
    # bytes exist on disk (pending with a file is common if the worker never flipped status).
    # Only rejected/failed without a file fall back to pending + guidance.
    if at in ("image", "video", "audio"):
        if local_path is not None:
            if a.status in ("pending", "rejected", "failed"):
                a.status = "succeeded"
                a.error_message = None
        elif a.status in ("rejected", "failed"):
            a.status = "pending"
            a.error_message = (
                "Approved but no readable media file under the configured storage root — "
                "check LOCAL_STORAGE_ROOT / paths or regenerate."
            )[:2000]
    pj = dict(a.params_json) if isinstance(a.params_json, dict) else {}
    pj.pop("rejection", None)
    a.params_json = pj
    db.commit()
    db.refresh(a)
    log.info("asset_approved", asset_id=str(asset_id))
    return {"data": AssetOut.model_validate(a).model_dump(mode="json"), "meta": meta}


@router.post("/assets/{asset_id}/reject")
def reject_asset(
    asset_id: UUID,
    body: AssetRejectBody | None = Body(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    a = _asset_or_404(db, settings, asset_id)
    eff = body or AssetRejectBody()
    a.approved_at = None
    a.status = "rejected"
    pj = dict(a.params_json) if isinstance(a.params_json, dict) else {}
    pj["rejection"] = {
        "reason": (eff.reason or "")[:8000],
        "rejected_at": datetime.now(timezone.utc).isoformat(),
    }
    a.params_json = pj
    db.commit()
    db.refresh(a)
    log.info("asset_rejected", asset_id=str(asset_id))
    return {"data": AssetOut.model_validate(a).model_dump(mode="json"), "meta": meta}


@router.get("/assets/{asset_id}/content")
def get_asset_content(
    asset_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    a = _asset_or_404(db, settings, asset_id)
    root = Path(settings.local_storage_root).resolve()
    p = _resolve_asset_local_path(a, storage_root=root)
    if p is None:
        log.warning(
            "asset_content_missing",
            asset_id=str(asset_id),
            storage_root=str(root),
            asset_status=a.status,
            preview_url=(a.preview_url or "")[:120],
            storage_url=(a.storage_url or "")[:120],
            storage_key=(a.params_json or {}).get("storage_key") if isinstance(a.params_json, dict) else None,
        )
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "asset file missing on disk"})
    return file_response_local_media(p)


@router.get("/scenes/{scene_id}/assets")
def list_scene_assets(
    scene_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _scene_or_404(db, settings, scene_id)
    rows = db.scalars(
        select(Asset)
        .where(Asset.scene_id == scene_id)
        .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
    ).all()
    return {
        "data": {"assets": [AssetOut.model_validate(a).model_dump(mode="json") for a in rows]},
        "meta": meta,
    }


@router.put("/scenes/{scene_id}/assets/sequence")
def put_scene_asset_sequence(
    scene_id: UUID,
    body: SceneAssetSequenceBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Set playback order: ``asset_ids`` first (indices 0..n-1), then any other scene assets by prior order."""
    _scene_or_404(db, settings, scene_id)
    ordered_ids = list(body.asset_ids)
    if len(ordered_ids) != len(set(ordered_ids)):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_SEQUENCE", "message": "asset_ids must not contain duplicates"},
        )
    assets_ordered: list[Asset] = []
    for aid in ordered_ids:
        a = db.get(Asset, aid)
        if not a or a.scene_id != scene_id or a.tenant_id != settings.default_tenant_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_ASSET", "message": f"asset not in scene: {aid}"},
            )
        assets_ordered.append(a)
    all_assets = list(db.scalars(select(Asset).where(Asset.scene_id == scene_id)).all())
    in_set = set(ordered_ids)
    rest = [a for a in all_assets if a.id not in in_set]
    rest.sort(key=lambda x: (x.timeline_sequence, x.created_at))
    for i, a in enumerate(assets_ordered):
        a.timeline_sequence = i
    base = len(assets_ordered)
    for j, a in enumerate(rest):
        a.timeline_sequence = base + j
    db.commit()
    rows = db.scalars(
        select(Asset)
        .where(Asset.scene_id == scene_id)
        .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
    ).all()
    log.info("scene_asset_sequence_updated", scene_id=str(scene_id), count=len(rows))
    return {
        "data": {"assets": [AssetOut.model_validate(a).model_dump(mode="json") for a in rows]},
        "meta": meta,
    }


@router.post("/scenes/{scene_id}/upload-clip")
async def upload_scene_clip(
    scene_id: UUID,
    file: UploadFile = File(...),
    clip_kind: str = Form("auto"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Upload image, video, or audio for a scene; stored as ``assets/<project>/<scene>/<asset_id>.<ext>``.

    Video and audio must be at most ~10 seconds (measured with ffprobe). Still images are unlimited;
    animated images are limited to 10 seconds of decoded timeline.
    """
    sc = _scene_or_404(db, settings, scene_id)
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    project_id = ch.project_id

    hint = (clip_kind or "auto").strip().lower()
    if hint not in ("auto", "image", "video", "audio"):
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_CLIP_KIND", "message": "clip_kind must be auto, image, video, or audio"},
        )

    try:
        validate_explicit_clip_kind(kind_hint=clip_kind, filename=file.filename)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={"code": "KIND_MISMATCH", "message": str(e)},
        ) from e

    raw_parts: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"code": "TOO_LARGE", "message": f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"},
            )
        raw_parts.append(chunk)
    raw = b"".join(raw_parts)
    if len(raw) < 16:
        raise HTTPException(status_code=422, detail={"code": "EMPTY", "message": "uploaded file too small"})

    orig_name = file.filename or "upload.bin"
    asset_kind, ext_guess = classify_from_filename_and_hint(orig_name, kind_hint=hint if hint != "auto" else None)
    suffix = ext_guess if ext_guess.startswith(".") else f".{ext_guess}"

    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        if tmp_path.suffix.lower() in AMBIGUOUS_EXTS and asset_kind == "video":
            asset_kind = refine_ambiguous_kind(
                tmp_path,
                ffprobe_bin=(settings.ffprobe_bin or "ffprobe").strip() or "ffprobe",
                initial=asset_kind,
            )

        ffprobe_bin = (settings.ffprobe_bin or "ffprobe").strip() or "ffprobe"
        try:
            measured_sec = assert_clip_duration_within_limit(tmp_path, asset_kind=asset_kind, ffprobe_bin=ffprobe_bin)
        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail={"code": "CLIP_TOO_LONG_OR_INVALID", "message": str(e)},
            ) from e

        norm_ext = normalized_extension(asset_kind, suffix)
        asset_id = uuid.uuid4()
        storage_key = f"assets/{project_id}/{scene_id}/{asset_id}{norm_ext}"

        storage = FilesystemStorage(settings.local_storage_root)
        ct = file.content_type
        if not ct or ct == "application/octet-stream":
            guessed, _ = mimetypes.guess_type(orig_name)
            ct = guessed or (
                "audio/mpeg"
                if asset_kind == "audio"
                else ("video/mp4" if asset_kind == "video" else "image/jpeg")
            )
        file_url = storage.put_bytes(storage_key, raw, content_type=ct)

        mx = db.scalar(select(func.max(Asset.timeline_sequence)).where(Asset.scene_id == scene_id))
        next_seq = int(mx or -1) + 1

        params: dict[str, object] = {
            "storage_key": storage_key,
            "source_filename": orig_name[:500],
            "upload": True,
        }
        if measured_sec is not None:
            params["duration_sec"] = round(float(measured_sec), 4)

        a = Asset(
            id=asset_id,
            tenant_id=settings.default_tenant_id,
            scene_id=scene_id,
            project_id=project_id,
            asset_type=asset_kind,
            status="succeeded",
            generation_tier="preview",
            provider="upload",
            model_name=None,
            params_json=params,
            storage_url=file_url,
            preview_url=file_url,
            error_message=None,
            timeline_sequence=next_seq,
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        log.info(
            "scene_clip_uploaded",
            scene_id=str(scene_id),
            asset_id=str(asset_id),
            asset_type=asset_kind,
            storage_key=storage_key,
        )
        return {"data": AssetOut.model_validate(a).model_dump(mode="json"), "meta": meta}
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
