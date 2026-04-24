"""Project-level picture geometry: 16:9 landscape vs 9:16 portrait (shorts)."""

from __future__ import annotations

from typing import Literal

FrameAspectRatio = Literal["16:9", "9:16"]

ClipFrameFit = Literal["center_crop", "letterbox"]

_VALID = frozenset({"16:9", "9:16"})
_VALID_CLIP_FIT = frozenset({"center_crop", "letterbox"})


def coerce_frame_aspect_ratio(raw: str | None) -> FrameAspectRatio:
    s = (raw or "").strip()
    if s in _VALID:
        return s  # type: ignore[return-value]
    return "16:9"


def coerce_clip_frame_fit(raw: str | None) -> ClipFrameFit:
    """How stills / Pexels clips are fitted to the project frame before export."""
    s = (raw or "").strip().lower()
    if s in _VALID_CLIP_FIT:
        return s  # type: ignore[return-value]
    return "center_crop"


def frame_pixel_size(aspect: str | None) -> tuple[int, int]:
    """Return (width, height) for pipeline normalize / local FFmpeg (short side ≈ 720p)."""
    if coerce_frame_aspect_ratio(aspect) == "9:16":
        return (720, 1280)
    return (1280, 720)


def fal_resolution_string(aspect: str | None) -> str:
    w, h = frame_pixel_size(aspect)
    return f"{w}x{h}"


def fal_image_size_enum(aspect: str | None) -> str:
    return "portrait_9_16" if coerce_frame_aspect_ratio(aspect) == "9:16" else "landscape_16_9"


def fal_aspect_ratio_string(aspect: str | None) -> str:
    return "9:16" if coerce_frame_aspect_ratio(aspect) == "9:16" else "16:9"


def image_prompt_aspect_phrase(aspect: str | None) -> str:
    """Short phrase for scene-plan image prompt boilerplate."""
    return (
        "9:16 vertical portrait frame, one frozen moment in time"
        if coerce_frame_aspect_ratio(aspect) == "9:16"
        else "16:9 widescreen landscape frame, one frozen moment in time"
    )
