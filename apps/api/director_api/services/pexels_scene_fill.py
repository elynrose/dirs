"""Pexels stock autofill per scene (full-video media tail: ``auto_images`` / ``auto_videos``)."""

from __future__ import annotations

import asyncio
import time
from typing import Literal
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.api.schemas.phase3 import ImportPexelsBody
from director_api.config import Settings
from director_api.db.models import Asset, Chapter, Project, Scene
from director_api.providers.pexels_client import search_photos_sync, search_videos_sync
from director_api.services.pexels_import_execute import PexelsImportError, execute_pexels_scene_import
from director_api.services.pexels_import_support import pexels_api_key_from_settings

log = structlog.get_logger(__name__)

_MediaKind = Literal["photo", "video"]


def _scene_has_pexels_asset(db: Session, scene_id: UUID) -> bool:
    n = db.scalar(
        select(func.count())
        .select_from(Asset)
        .where(
            Asset.scene_id == scene_id,
            Asset.provider == "pexels",
            Asset.status == "succeeded",
        )
    )
    return int(n or 0) > 0


def _build_pexels_query_for_scene(scene: Scene, project: Project) -> str:
    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    terms = pp.get("stock_search_terms")
    if isinstance(terms, list) and terms:
        parts = [str(t).strip() for t in terms if t and str(t).strip()][:3]
        q = " ".join(parts)[:200].strip()
        if q:
            return q
    for fallback in (scene.purpose, scene.narration_text, project.topic):
        s = (fallback or "").strip()
        if len(s) >= 8:
            return s[:200]
    return "documentary b-roll"


def _normalize_media_mode(settings: Settings) -> str:
    m = str(getattr(settings, "agent_run_pexels_scene_media_mode", "photos") or "photos").strip().lower()
    return m if m in ("photos", "videos", "both") else "photos"


def _search_interval_sec(settings: Settings) -> float:
    try:
        f = float(getattr(settings, "agent_run_pexels_scene_search_interval_sec", 2.0) or 0.0)
    except (TypeError, ValueError):
        return 2.0
    return max(0.0, min(f, 120.0))


def _first_pexels_hit(
    *,
    api_key: str,
    query: str,
    mode: str,
) -> tuple[_MediaKind, int] | None:
    """Return (photo|video, pexels_id) for the first search hit, respecting ``mode``."""
    if mode in ("photos", "both"):
        try:
            data = search_photos_sync(api_key=api_key, query=query, per_page=8)
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError, TypeError) as e:
            log.warning("pexels_scene_fill_photo_search_failed", error=str(e)[:240])
            data = {}
        for row in data.get("results") or []:
            if not isinstance(row, dict):
                continue
            pid = row.get("pexels_id")
            if isinstance(pid, int) and pid > 0:
                return ("photo", pid)
    if mode in ("videos", "both"):
        try:
            data = search_videos_sync(api_key=api_key, query=query, per_page=8)
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError, TypeError) as e:
            log.warning("pexels_scene_fill_video_search_failed", error=str(e)[:240])
            data = {}
        for row in data.get("results") or []:
            if not isinstance(row, dict):
                continue
            vid = row.get("pexels_id")
            if isinstance(vid, int) and vid > 0:
                return ("video", vid)
    return None


def maybe_fill_pexels_for_project_scenes(db: Session, settings: Settings, project: Project) -> None:
    """Run Pexels autofill for every chapter in project order (used from agent ``auto_images`` / ``auto_videos``)."""
    chapters = list(
        db.scalars(
            select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)
        ).all()
    )
    for ch in chapters:
        maybe_fill_pexels_after_chapter_scene_plan(db, settings, ch, project)


def maybe_fill_pexels_after_chapter_scene_plan(
    db: Session,
    settings: Settings,
    chapter: Chapter,
    project: Project,
    *,
    scene_ids_only: list[UUID] | None = None,
) -> None:
    """When enabled + API key present, import first Pexels result per target scene in one chapter (best-effort)."""
    if not bool(getattr(settings, "agent_run_use_pexels_for_scenes", False)):
        return
    api_key = pexels_api_key_from_settings(settings)
    if not api_key:
        log.info("pexels_scene_fill_skipped_no_api_key", chapter_id=str(chapter.id))
        return

    mode = _normalize_media_mode(settings)
    interval = _search_interval_sec(settings)

    db.refresh(chapter, attribute_names=["scenes"])
    scenes = sorted(chapter.scenes, key=lambda s: int(s.order_index or 0))
    if scene_ids_only:
        wanted = set(scene_ids_only)
        scenes = [s for s in scenes if s.id in wanted]

    first = True
    for sc in scenes:
        if _scene_has_pexels_asset(db, sc.id):
            continue
        if not first and interval > 0:
            time.sleep(interval)
        first = False

        query = _build_pexels_query_for_scene(sc, project)
        hit = _first_pexels_hit(api_key=api_key, query=query, mode=mode)
        if not hit:
            log.warning(
                "pexels_scene_fill_no_results",
                scene_id=str(sc.id),
                query_preview=query[:120],
                mode=mode,
            )
            continue
        kind, pexels_id = hit
        body = ImportPexelsBody(
            kind="photo" if kind == "photo" else "video",
            pexels_id=pexels_id,
            video_trim_target="10",
        )
        try:
            asyncio.run(
                execute_pexels_scene_import(
                    db,
                    settings,
                    sc.id,
                    body,
                    api_key=api_key,
                )
            )
        except PexelsImportError as e:
            log.warning(
                "pexels_scene_fill_import_failed",
                scene_id=str(sc.id),
                code=e.code,
                message=e.message[:400],
            )
        except RuntimeError as e:
            # Nested event loop (unlikely in Celery sync worker).
            log.warning("pexels_scene_fill_asyncio_failed", scene_id=str(sc.id), error=str(e)[:400])
