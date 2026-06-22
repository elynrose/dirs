"""Opening hook as scene 0 in the first chapter (cover still + spoken hook)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import Asset, Chapter, Project, Scene
from director_api.services.phase3 import default_scene_negative_prompt_for_project
from director_api.services.publish_pack import resolve_thumbnail_content_path
from director_api.services.research_service import sanitize_jsonb_text
from director_api.storage.filesystem import FilesystemStorage
from director_api.style_presets import effective_visual_style

log = structlog.get_logger(__name__)

HOOK_SCENE_ROLE = "hook"


def is_hook_scene(scene: Scene) -> bool:
    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    return pp.get("scene_role") == HOOK_SCENE_ROLE


def find_hook_scene(db: Session, project_id: uuid.UUID) -> Scene | None:
    ch = _first_chapter(db, project_id)
    if not ch:
        return None
    for sc in sorted(ch.scenes, key=lambda s: int(s.order_index or 0)):
        if is_hook_scene(sc):
            return sc
    return None


def _first_chapter(db: Session, project_id: uuid.UUID) -> Chapter | None:
    return db.scalars(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index).limit(1)
    ).first()


def order_index_offset_for_chapter_replan(db: Session, chapter: Chapter) -> int:
    """When the first chapter keeps a hook scene at index 0, planned scenes start at 1."""
    fc = _first_chapter(db, chapter.project_id)
    if fc and fc.id == chapter.id and find_hook_scene(db, chapter.project_id):
        return 1
    return 0


def delete_chapter_scenes_preserving_hook(db: Session, chapter: Chapter, project_id: uuid.UUID) -> None:
    fc = _first_chapter(db, project_id)
    preserve_hook = fc is not None and fc.id == chapter.id
    for sc in list(chapter.scenes):
        if preserve_hook and is_hook_scene(sc):
            continue
        db.delete(sc)
    db.flush()


def _hook_narration(project: Project, *, narration_override: str | None = None) -> str:
    text = (narration_override if narration_override is not None else project.opening_hook_text or "").strip()
    return sanitize_jsonb_text(text, 8000)


def _thumbnail_prompt(project: Project) -> str:
    pack = project.publish_pack_json if isinstance(project.publish_pack_json, dict) else {}
    prompt = str(pack.get("thumbnail_prompt") or "").strip()
    if prompt:
        return sanitize_jsonb_text(prompt, 4000)
    title = str(pack.get("youtube_title") or project.title or "").strip()
    vis = (project.visual_style or "cinematic documentary").strip()
    return sanitize_jsonb_text(
        f"YouTube documentary opening cover art, bold composition, topic: {title}, {vis}.",
        4000,
    )


def _estimate_planned_duration_sec(narration: str) -> int:
    words = max(1, len(narration.split()))
    return max(5, min(120, int(round(words / 130.0 * 60))))


def attach_publish_thumbnail_to_hook_scene(
    db: Session,
    project: Project,
    scene: Scene,
    settings: Settings,
) -> Asset | None:
    pack = project.publish_pack_json if isinstance(project.publish_pack_json, dict) else {}
    thumb_key = str(pack.get("thumbnail_storage_key") or "").strip()
    if not thumb_key:
        return None
    path = resolve_thumbnail_content_path(project, settings)
    if path is None or not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) < 64:
        return None

    for asset in list(scene.assets):
        params = asset.params_json if isinstance(asset.params_json, dict) else {}
        if params.get("source") == "publish_thumbnail" or (asset.provider or "") == "publish_thumbnail":
            db.delete(asset)
    db.flush()

    ext = path.suffix.lstrip(".").lower() or "png"
    content_type = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    asset_id = uuid.uuid4()
    storage = FilesystemStorage(settings.local_storage_root)
    key = f"assets/{project.id}/{scene.id}/{asset_id}.{ext}"
    url = storage.put_bytes(key, raw, content_type=content_type)
    next_seq = db.scalar(select(func.max(Asset.timeline_sequence)).where(Asset.scene_id == scene.id)) or -1
    asset = Asset(
        id=asset_id,
        tenant_id=project.tenant_id,
        scene_id=scene.id,
        project_id=project.id,
        asset_type="image",
        status="succeeded",
        generation_tier="production",
        provider="publish_thumbnail",
        model_name=None,
        params_json={"source": "publish_thumbnail", "thumbnail_storage_key": thumb_key},
        storage_url=url,
        preview_url=url,
        error_message=None,
        timeline_sequence=int(next_seq) + 1,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(asset)
    scene.status = "image_ready"
    scene.visual_type = "still"
    db.flush()
    return asset


def append_hook_scene(
    db: Session,
    project: Project,
    settings: Settings,
    *,
    narration_override: str | None = None,
) -> Scene | None:
    narration = _hook_narration(project, narration_override=narration_override)
    if len(narration) < 12:
        return None
    ch = _first_chapter(db, project.id)
    if not ch:
        raise ValueError("no chapters — run outline before hook scene")
    existing = find_hook_scene(db, project.id)
    if existing:
        db.delete(existing)
        db.flush()

    for sc in sorted(
        list(db.scalars(select(Scene).where(Scene.chapter_id == ch.id)).all()),
        key=lambda s: int(s.order_index or 0),
        reverse=True,
    ):
        sc.order_index = int(sc.order_index or 0) + 1

    vis = effective_visual_style(project.visual_style, settings)
    thumb_prompt = _thumbnail_prompt(project)
    pp: dict[str, Any] = {
        "scene_role": HOOK_SCENE_ROLE,
        "image_prompt": thumb_prompt,
        "video_prompt": sanitize_jsonb_text(f"Opening documentary hook, {vis}, subtle motion.", 3000),
        "negative_prompt": default_scene_negative_prompt_for_project(project, None),
        "use_publish_thumbnail_still": True,
    }
    sc = Scene(
        id=uuid.uuid4(),
        chapter_id=ch.id,
        order_index=0,
        purpose="Opening hook",
        planned_duration_sec=_estimate_planned_duration_sec(narration),
        narration_text=narration,
        visual_type="still",
        prompt_package_json=pp,
        continuity_tags_json=["hook", "opening"],
        status="planned",
    )
    db.add(sc)
    db.flush()
    attach_publish_thumbnail_to_hook_scene(db, project, sc, settings)
    log.info("hook_scene_appended", project_id=str(project.id), scene_id=str(sc.id), chapter_id=str(ch.id))
    return sc


def sync_hook_scene_from_project(db: Session, project: Project, settings: Settings) -> Scene | None:
    """Update or create the hook scene when opening_hook_text / thumbnail change."""
    narration = _hook_narration(project)
    if len(narration) < 12:
        remove_hook_scene(db, project.id)
        return None
    ch = _first_chapter(db, project.id)
    if not ch:
        return None
    scene_count = db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch.id)) or 0
    existing = find_hook_scene(db, project.id)
    if existing:
        existing.narration_text = narration
        existing.planned_duration_sec = _estimate_planned_duration_sec(narration)
        attach_publish_thumbnail_to_hook_scene(db, project, existing, settings)
        db.flush()
        return existing
    if int(scene_count) == 0:
        return None
    return append_hook_scene(db, project, settings)


def remove_hook_scene(db: Session, project_id: uuid.UUID) -> bool:
    sc = find_hook_scene(db, project_id)
    if not sc:
        return False
    ch_id = sc.chapter_id
    db.delete(sc)
    db.flush()
    for other in sorted(
        list(db.scalars(select(Scene).where(Scene.chapter_id == ch_id)).all()),
        key=lambda s: int(s.order_index or 0),
    ):
        idx = int(other.order_index or 0)
        if idx > 0:
            other.order_index = idx - 1
    db.flush()
    return True
