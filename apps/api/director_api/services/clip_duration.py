"""Per-scene clip length for video generation (planned beat vs workspace default)."""

from __future__ import annotations

from typing import Any

from director_api.db.models import Scene


def clip_seconds_for_scene(
    settings: Any,
    scene: Scene,
    *,
    provider: str | None = None,
    fal_model: str | None = None,
    fallback_sec: float | None = None,
) -> float:
    """Seconds for one generative clip on this scene, clamped for the active provider."""
    sec: float | None = None
    if scene.planned_duration_sec is not None:
        try:
            sec = float(scene.planned_duration_sec)
        except (TypeError, ValueError):
            sec = None
    if sec is None and fallback_sec is not None:
        try:
            sec = float(fallback_sec)
        except (TypeError, ValueError):
            sec = None
    if sec is None:
        try:
            sec = float(getattr(settings, "scene_clip_duration_sec", 10) or 10)
        except (TypeError, ValueError):
            sec = 10.0
    sec = max(1.0, min(600.0, float(sec)))

    p = (provider or "").strip().lower()
    if p in ("comfyui_wan", "comfyui"):
        return min(sec, 6.0)
    if p == "fal":
        _ = fal_model
        return min(sec, 15.0)
    return sec
