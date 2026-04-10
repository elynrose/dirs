"""Scene visual timing vs per-scene narration (VO) length."""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import NarrationTrack, Scene
from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file
from ffmpeg_pipelines.probe import ffprobe_duration_seconds


def scene_vo_tail_padding_sec_from_settings(settings: Any) -> float:
    """Tail padding from merged Settings (env + app_settings); clamped for safety."""
    try:
        v = float(getattr(settings, "scene_vo_tail_padding_sec", 5.0) or 5.0)
    except (TypeError, ValueError):
        return 5.0
    if v != v:  # NaN
        return 5.0
    return max(0.0, min(120.0, v))


def min_planned_duration_int_for_narration_sec(narr_sec: float, *, tail_padding_sec: float = 5.0) -> int:
    """Integer ``planned_duration_sec`` so the scene budget is at least narration + tail padding (3–600)."""
    narr = max(0.0, float(narr_sec))
    pad = max(0.0, float(tail_padding_sec))
    need = narr + pad
    return max(3, min(600, int(math.ceil(need))))


def _latest_scene_narration_duration_sec(
    db: Session,
    *,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    storage_root: Path,
    ffprobe_bin: str,
    timeout_sec: float,
) -> float | None:
    nt = db.scalar(
        select(NarrationTrack)
        .where(
            NarrationTrack.project_id == project_id,
            NarrationTrack.scene_id == scene_id,
            NarrationTrack.audio_url.isnot(None),
        )
        .order_by(NarrationTrack.created_at.desc())
    )
    if not nt:
        return None
    if nt.duration_sec is not None:
        try:
            d = float(nt.duration_sec)
            if d > 0:
                return d
        except (TypeError, ValueError):
            pass
    p = path_from_storage_url(nt.audio_url or "", storage_root=storage_root)
    if p is None or not path_is_readable_file(p):
        return None
    d = float(ffprobe_duration_seconds(p, ffprobe_bin=ffprobe_bin, timeout_sec=min(timeout_sec, 120.0)))
    return d if d > 0 else None


def get_scene_narration_audio_duration_sec(
    db: Session,
    *,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    storage_root: Path,
    ffprobe_bin: str,
    timeout_sec: float,
) -> float | None:
    """Duration in seconds of the latest scene VO file (DB field or ffprobe), or None."""
    return _latest_scene_narration_duration_sec(
        db,
        project_id=project_id,
        scene_id=scene_id,
        storage_root=storage_root,
        ffprobe_bin=ffprobe_bin,
        timeout_sec=timeout_sec,
    )


def effective_scene_visual_budget_sec(
    db: Session,
    *,
    scene: Scene,
    project_id: uuid.UUID,
    base_clip_sec: float,
    storage_root: Path,
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 120.0,
    tail_padding_sec: float = 5.0,
) -> float:
    """
    Seconds of visual timeline to allocate for one scene's primary beat.

    Uses ``max(planned_duration_sec or base_clip, narration_length + tail_padding_sec)``
    when scene VO audio exists; otherwise the planned/default clip length.
    """
    base = float(scene.planned_duration_sec or base_clip_sec)
    narr = _latest_scene_narration_duration_sec(
        db,
        project_id=project_id,
        scene_id=scene.id,
        storage_root=storage_root,
        ffprobe_bin=ffprobe_bin,
        timeout_sec=timeout_sec,
    )
    if narr is None:
        return max(0.25, base)
    pad = max(0.0, float(tail_padding_sec))
    need = narr + pad
    return max(base, need)


def bump_scene_planned_duration_for_narration(
    db: Session,
    scene: Scene,
    narration_sec: float,
    *,
    tail_padding_sec: float = 5.0,
) -> bool:
    """Ensure ``planned_duration_sec`` covers VO + tail padding. Returns True if the row was raised."""
    target = min_planned_duration_int_for_narration_sec(narration_sec, tail_padding_sec=tail_padding_sec)
    cur = int(scene.planned_duration_sec or 0)
    if cur < target:
        scene.planned_duration_sec = target
        return True
    return False
