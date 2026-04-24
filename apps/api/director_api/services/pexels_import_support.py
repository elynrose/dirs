"""Shared helpers for Pexels imports (trim caps, API key read)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import Scene
from director_api.services.scene_clip_upload import MAX_CLIP_SECONDS
from director_api.services.scene_timeline_duration import get_scene_narration_audio_duration_sec

PEXELS_TRIM_DURATION_SLACK_SEC = 0.06
PEXELS_TRIM_MIN_SEC = 0.5


def studio_default_clip_sec(settings: Settings) -> float:
    """Studio default clip length (5 or 10) from merged settings."""
    try:
        v = int(getattr(settings, "scene_clip_duration_sec", 10) or 10)
    except (TypeError, ValueError):
        return 10.0
    return 5.0 if v == 5 else 10.0


def resolve_pexels_trim_max_sec(
    db: Session,
    *,
    settings: Settings,
    scene: Scene,
    project_id: UUID,
    trim_target: str,
    storage_root: Path,
    ffprobe_bin: str,
) -> float:
    """Resolve requested trim cap in seconds (capped at ``MAX_CLIP_SECONDS``)."""
    tt = (trim_target or "10").strip().lower()
    if tt == "5":
        return min(5.0, MAX_CLIP_SECONDS)
    if tt == "10":
        return MAX_CLIP_SECONDS
    if tt == "scene_narration":
        narr = get_scene_narration_audio_duration_sec(
            db,
            project_id=project_id,
            scene_id=scene.id,
            storage_root=storage_root,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=90.0,
        )
        if narr is not None and narr > 0:
            return min(float(narr), MAX_CLIP_SECONDS)
        pd = scene.planned_duration_sec
        if pd is not None:
            try:
                p = float(pd)
                if p > 0:
                    return min(p, MAX_CLIP_SECONDS)
            except (TypeError, ValueError):
                pass
        return min(studio_default_clip_sec(settings), MAX_CLIP_SECONDS)
    return MAX_CLIP_SECONDS


def pexels_api_key_from_settings(settings: Settings) -> str:
    return (getattr(settings, "pexels_api_key", None) or "").strip()
