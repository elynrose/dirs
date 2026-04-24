"""Scene visual timing vs per-scene narration (VO) length."""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import NarrationTrack, Scene
from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file
from ffmpeg_pipelines.probe import ffprobe_duration_seconds

DEFAULT_SCENE_VO_TAIL_PADDING_SEC = 1.5


def scene_vo_tail_padding_sec_from_settings(settings: Any) -> float:
    """Tail padding from merged Settings (env + app_settings); clamped for safety."""
    d = DEFAULT_SCENE_VO_TAIL_PADDING_SEC
    try:
        v = float(getattr(settings, "scene_vo_tail_padding_sec", d) or d)
    except (TypeError, ValueError):
        return d
    if v != v:  # NaN
        return d
    return max(0.0, min(120.0, v))


def min_planned_duration_int_for_narration_sec(
    narr_sec: float, *, tail_padding_sec: float = DEFAULT_SCENE_VO_TAIL_PADDING_SEC
) -> int:
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


def _latest_chapter_narration_total_sec(
    db: Session,
    *,
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
    storage_root: Path,
    ffprobe_bin: str,
    timeout_sec: float,
) -> float | None:
    """Total duration of the latest chapter-level VO (``scene_id`` is NULL), or None."""
    nt = db.scalar(
        select(NarrationTrack)
        .where(
            NarrationTrack.project_id == project_id,
            NarrationTrack.chapter_id == chapter_id,
            NarrationTrack.scene_id.is_(None),
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


def get_export_narration_budget_sec_for_scene(
    db: Session,
    *,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    storage_root: Path,
    ffprobe_bin: str,
    timeout_sec: float,
) -> float | None:
    """Seconds to budget for a scene's first timeline clip when expanding export visuals.

    Prefer per-scene VO. If missing, use an equal split of the chapter-level VO across scenes in
    that chapter (chapter TTS stores one file on ``NarrationTrack`` with ``scene_id`` NULL).
    """
    per_scene = _latest_scene_narration_duration_sec(
        db,
        project_id=project_id,
        scene_id=scene_id,
        storage_root=storage_root,
        ffprobe_bin=ffprobe_bin,
        timeout_sec=timeout_sec,
    )
    if per_scene is not None:
        return per_scene
    sc = db.get(Scene, scene_id)
    if not sc or not sc.chapter_id:
        return None
    ch_id = sc.chapter_id
    n_scenes = int(
        db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch_id)) or 0
    )
    if n_scenes <= 0:
        return None
    total = _latest_chapter_narration_total_sec(
        db,
        project_id=project_id,
        chapter_id=ch_id,
        storage_root=storage_root,
        ffprobe_bin=ffprobe_bin,
        timeout_sec=timeout_sec,
    )
    if total is None or total <= 0:
        return None
    return float(total) / float(n_scenes)


def effective_scene_visual_budget_sec(
    db: Session,
    *,
    scene: Scene,
    project_id: uuid.UUID,
    base_clip_sec: float,
    storage_root: Path,
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 120.0,
    tail_padding_sec: float = DEFAULT_SCENE_VO_TAIL_PADDING_SEC,
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
    tail_padding_sec: float = DEFAULT_SCENE_VO_TAIL_PADDING_SEC,
) -> bool:
    """Ensure ``planned_duration_sec`` covers VO + tail padding. Returns True if the row was raised."""
    target = min_planned_duration_int_for_narration_sec(narration_sec, tail_padding_sec=tail_padding_sec)
    cur = int(scene.planned_duration_sec or 0)
    if cur < target:
        scene.planned_duration_sec = target
        return True
    return False
