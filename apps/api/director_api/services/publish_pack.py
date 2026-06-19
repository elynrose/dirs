"""Project publish pack — thumbnail metadata/image and opening hook script."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.agents.phase2_publish_llm import (
    generate_opening_hook_llm,
    generate_publish_thumbnail_pack_llm,
)
from director_api.config import Settings, get_settings
from director_api.db.models import Chapter, Project, ResearchDossier
from director_api.providers.media_fal import generate_scene_image
from director_api.services.image_provider_routing import resolve_image_provider
from director_api.services.project_frame import coerce_frame_aspect_ratio
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.services import phase2 as phase2_svc
from director_api.services.research_service import sanitize_jsonb_text
from director_api.storage.filesystem import FilesystemStorage
from director_api.style_presets import effective_narration_style

log = structlog.get_logger(__name__)

DEFAULT_OPENING_HOOK = (
    "Before we dive in — this story goes places you might not expect. "
    "Stay with us; the evidence only gets more compelling from here."
)

THUMBNAIL_ASPECT = "16:9"


def _latest_dossier(db: Session, project_id: uuid.UUID) -> ResearchDossier | None:
    return db.scalars(
        select(ResearchDossier)
        .where(ResearchDossier.project_id == project_id)
        .order_by(ResearchDossier.version.desc())
        .limit(1)
    ).first()


def publish_pack_done(pack: dict[str, Any] | None) -> bool:
    if not isinstance(pack, dict):
        return False
    key = str(pack.get("thumbnail_storage_key") or "").strip()
    title = str(pack.get("youtube_title") or "").strip()
    return bool(key and title)


def merge_publish_pack(project: Project, patch: dict[str, Any]) -> dict[str, Any]:
    base = dict(project.publish_pack_json or {})
    base.update(patch)
    base["updated_at"] = datetime.now(timezone.utc).isoformat()
    return base


def _chapter_titles(db: Session, project_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)
    ).all()
    return [str(ch.title or "").strip() for ch in rows if str(ch.title or "").strip()]


def _generate_thumbnail_image(
    settings: Settings,
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    prompt: str,
    provider: str,
    frame_aspect_ratio: str,
    negative_prompt: str = "",
) -> tuple[str, str, str]:
    """Returns (storage_key, file_url, provider_used)."""
    aspect = coerce_frame_aspect_ratio(frame_aspect_ratio)
    if provider == "comfyui":
        from director_api.providers.media_comfyui import generate_scene_image_comfyui

        result = generate_scene_image_comfyui(
            settings,
            prompt,
            negative_prompt=negative_prompt or None,
            frame_aspect_ratio=aspect,
        )
    elif provider == "placeholder":
        from director_api.providers.media_placeholder import render_placeholder_scene_png_bytes

        raw = render_placeholder_scene_png_bytes(
            ffmpeg_bin=(settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg",
            timeout_sec=min(float(settings.ffmpeg_timeout_sec), 120.0),
            width=1280 if aspect == "16:9" else 720,
            height=720 if aspect == "16:9" else 1280,
        )
        result = {"ok": True, "bytes": raw, "content_type": "image/png", "provider": "placeholder"}
    else:
        result = generate_scene_image(
            settings,
            prompt,
            frame_aspect_ratio=aspect,
            negative_prompt=negative_prompt or None,
        )
    if not result.get("ok") or not result.get("bytes"):
        raise ValueError(str(result.get("error") or result.get("detail") or "thumbnail image generation failed"))
    raw = result["bytes"]
    ct = str(result.get("content_type") or "image/png")
    ext = "jpg" if "jpeg" in ct else "png"
    asset_id = uuid.uuid4()
    key = f"assets/{tenant_id}/{project_id}/thumbnail/{asset_id}.{ext}"
    storage = FilesystemStorage(settings.local_storage_root)
    file_url = storage.put_bytes(key, raw, content_type=ct)
    provider_used = str(result.get("provider") or provider)
    return key, file_url, provider_used


def thumbnail_core(db: Session, project: Project, settings: Settings) -> None:
    """LLM metadata + still via workspace image settings (ComfyUI/Fal/placeholder) → publish_pack_json."""
    settings = resolve_runtime_settings(db, get_settings(), project.tenant_id, user_id=None)
    llm_u: list[dict[str, Any]] = []
    meta = generate_publish_thumbnail_pack_llm(
        project_title=project.title,
        project_topic=project.topic,
        chapter_titles=_chapter_titles(db, project.id),
        settings=settings,
        usage_sink=llm_u,
    )
    if not meta:
        meta = {
            "youtube_title": sanitize_jsonb_text(project.title, 100),
            "youtube_description": sanitize_jsonb_text(project.topic, 500)[:500],
            "thumbnail_prompt": sanitize_jsonb_text(
                f"YouTube documentary thumbnail, bold composition, topic: {project.title}",
                2000,
            ),
        }
    storage_key = ""
    provider = "none"
    resolved = resolve_image_provider(project, settings, prefer_workspace_settings=True)
    frame_aspect = str(getattr(project, "frame_aspect_ratio", None) or THUMBNAIL_ASPECT)
    if resolved.provider in ("comfyui", "fal", "placeholder"):
        log.info(
            "thumbnail_image_dispatch",
            project_id=str(project.id),
            resolved_provider=resolved.provider,
            requested_provider=resolved.requested,
            model_name=resolved.model_name,
            comfyui_base_url=(settings.comfyui_base_url or "")[:80],
            fal_key_configured=bool((settings.fal_key or "").strip()),
        )
        storage_key, _url, provider = _generate_thumbnail_image(
            settings,
            tenant_id=project.tenant_id,
            project_id=project.id,
            prompt=meta["thumbnail_prompt"],
            provider=resolved.provider,
            frame_aspect_ratio=frame_aspect,
        )
    else:
        log.warning(
            "thumbnail_image_skipped_no_provider",
            project_id=str(project.id),
            requested_provider=resolved.requested,
            active_image_provider=getattr(settings, "active_image_provider", None),
        )

    pack = merge_publish_pack(
        project,
        {
            "youtube_title": meta["youtube_title"],
            "youtube_description": meta.get("youtube_description") or "",
            "thumbnail_prompt": meta["thumbnail_prompt"],
            "thumbnail_storage_key": storage_key or None,
            "thumbnail_provider": provider,
            "source": "generated",
        },
    )
    project.publish_pack_json = pack
    flag_modified(project, "publish_pack_json")
    project.workflow_phase = "thumbnail_ready"
    db.flush()
    if llm_u:
        from director_api.tasks.worker_runtime import _flush_llm_usage

        _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)


def save_thumbnail_upload(
    db: Session,
    project: Project,
    settings: Settings,
    *,
    raw: bytes,
    content_type: str,
    youtube_title: str | None = None,
    youtube_description: str | None = None,
) -> dict[str, Any]:
    if len(raw) < 64:
        raise ValueError("uploaded thumbnail too small")
    ext = "jpg" if "jpeg" in (content_type or "") else "png"
    asset_id = uuid.uuid4()
    key = f"assets/{project.tenant_id}/{project.id}/thumbnail/{asset_id}.{ext}"
    storage = FilesystemStorage(settings.local_storage_root)
    storage.put_bytes(key, raw, content_type=content_type or "image/png")
    prior = dict(project.publish_pack_json or {})
    pack = merge_publish_pack(
        project,
        {
            "youtube_title": sanitize_jsonb_text(
                youtube_title or prior.get("youtube_title") or project.title, 100
            ),
            "youtube_description": sanitize_jsonb_text(
                youtube_description or prior.get("youtube_description") or project.topic, 5000
            ),
            "thumbnail_storage_key": key,
            "thumbnail_provider": "upload",
            "source": "upload",
        },
    )
    project.publish_pack_json = pack
    flag_modified(project, "publish_pack_json")
    project.workflow_phase = "thumbnail_ready"
    db.flush()
    return pack


def opening_hook_core(db: Session, project: Project, settings: Settings) -> None:
    dossier = _latest_dossier(db, project.id)
    dossier_body = (dossier.body_json if dossier else {}) or {}
    summary = ""
    if isinstance(dossier_body, dict):
        summary = str(dossier_body.get("summary") or "")
    first_ch = db.scalars(
        select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index).limit(1)
    ).first()
    excerpt = (first_ch.script_text or first_ch.summary or "")[:2500] if first_ch else ""
    director = phase2_svc.normalized_director_pack(project) if project.director_output_json else None
    nar = effective_narration_style(project.narration_style, settings, db=db, tenant_id=project.tenant_id)
    llm_u: list[dict[str, Any]] = []
    hook = generate_opening_hook_llm(
        project_title=project.title,
        project_topic=project.topic,
        director_pack=director,
        dossier_summary=summary,
        first_chapter_excerpt=excerpt,
        narration_style=nar,
        settings=settings,
        usage_sink=llm_u,
    )
    project.opening_hook_text = hook or DEFAULT_OPENING_HOOK
    project.workflow_phase = "hook_ready"
    db.flush()
    if llm_u:
        from director_api.tasks.worker_runtime import _flush_llm_usage

        _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)


def resolve_thumbnail_content_path(project: Project, settings: Settings):
    from director_api.storage.filesystem import FilesystemStorage, resolve_storage_path

    pack = project.publish_pack_json if isinstance(project.publish_pack_json, dict) else {}
    key = str(pack.get("thumbnail_storage_key") or "").strip()
    if not key:
        return None
    storage = FilesystemStorage(settings.local_storage_root)
    return resolve_storage_path(storage, key, tenant_id=project.tenant_id)
