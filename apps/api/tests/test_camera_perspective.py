"""Tests for per-scene camera perspective hints."""

from __future__ import annotations

from director_api.services import camera_perspective as cp


def test_stable_index_same_scene() -> None:
    a = cp.scene_camera_image_hint("scene-abc", 3)
    b = cp.scene_camera_image_hint("scene-abc", 3)
    assert a == b
    assert a.startswith("Camera perspective:")


def test_different_order_index_varies_hint() -> None:
    hints = {cp.scene_camera_image_hint("chapter-1", i) for i in range(12)}
    assert len(hints) >= 3


def test_inject_skips_when_angle_present() -> None:
    p = "Low-angle shot of a knight in a courtyard."
    out = cp.inject_camera_perspective_into_prompt(
        p, scene_key="s1", order_index=0, for_video=False, max_total=4000
    )
    assert out == p


def test_inject_prepends_hint() -> None:
    out = cp.inject_camera_perspective_into_prompt(
        "A market square at dawn.",
        scene_key="s2",
        order_index=1,
        for_video=False,
        max_total=4000,
    )
    assert out.startswith("Camera perspective:")
    assert "market square" in out


def test_inject_after_leading_shot_tag() -> None:
    out = cp.inject_camera_perspective_into_prompt(
        "[MS] Torchlit corridor, guard walking away.",
        scene_key="s3",
        order_index=2,
        for_video=False,
        max_total=4000,
    )
    assert out.startswith("[MS] Camera perspective:")
