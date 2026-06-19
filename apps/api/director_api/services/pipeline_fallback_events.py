"""Structured pipeline fallback events for agent runs (Studio + Telegram copy)."""

from __future__ import annotations

from typing import Any


def auto_videos_partial_failed_extra(*, generated: int, failed_scene_count: int) -> dict[str, Any]:
    """Extra ``steps_json`` fields when ``auto_videos`` ends ``partial_failed``."""
    if generated == 0 and failed_scene_count > 0:
        return {
            "failure_reason_summary": (
                "Video generation was enabled but no scene videos were created. "
                "Check your video provider (for example ComfyUI on port 8188). "
                "The timeline may still generate still images as a fallback."
            ),
        }
    return {}


def visual_heal_event_fields(
    *,
    scene_id: str,
    heal_kind: str = "timeline_image",
    auto_generate_scene_images: bool | None = None,
    auto_generate_scene_videos: bool | None = None,
) -> dict[str, Any]:
    """Fields for ``auto_timeline`` / ``visual_heal`` — distinct from normal ``auto_images``."""
    out: dict[str, Any] = {
        "scene_id": scene_id,
        "heal_kind": heal_kind,
        "summary": (
            "Timeline emergency image: scene had no video or image; generated a still so export can continue."
        ),
    }
    if auto_generate_scene_images is not None:
        out["auto_generate_scene_images"] = auto_generate_scene_images
    if auto_generate_scene_videos is not None:
        out["auto_generate_scene_videos"] = auto_generate_scene_videos
    return out
