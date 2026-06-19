"""Tests for still-motion resolution in rough-cut export."""

from __future__ import annotations

from director_api.tasks.worker_tasks import (
    _manifest_requires_still_motion_encode,
    _resolve_still_motion,
)


def test_resolve_still_motion_clip_override() -> None:
    out = _resolve_still_motion(
        timeline_json={"still_motion_mode": "zoom", "still_motion_source": "timeline_default"},
        clip={"still_motion": "pan"},
        scene_video_prompt="slow zoom in on the subject",
    )
    assert out == "pan"


def test_resolve_still_motion_scene_prompt() -> None:
    out = _resolve_still_motion(
        timeline_json={"still_motion_mode": "none", "still_motion_source": "scene_video_prompt"},
        clip=None,
        scene_video_prompt="camera pans left across the valley",
    )
    assert out == "pan"


def test_resolve_still_motion_timeline_default() -> None:
    out = _resolve_still_motion(
        timeline_json={"still_motion_mode": "zoom", "still_motion_source": "timeline_default"},
        clip=None,
        scene_video_prompt=None,
    )
    assert out == "zoom"


def test_manifest_requires_still_motion_encode() -> None:
    assert not _manifest_requires_still_motion_encode(
        [{"asset_type": "image", "still_motion": "none"}],
    )
    assert _manifest_requires_still_motion_encode(
        [{"asset_type": "image", "still_motion": "zoom"}],
    )
    assert not _manifest_requires_still_motion_encode(
        [{"asset_type": "video", "still_motion": "zoom"}],
    )
