from types import SimpleNamespace

import pytest

from director_api.services.phase3 import (
    MAX_MANUAL_SCENE_LINES,
    build_scene_plan_batch_from_lines,
    parse_manual_scene_lines,
)


def test_parse_manual_scene_lines_skips_blank_and_comments():
    text = "# intro\n\nLine one\n  \nLine two\n# skip\nLine three"
    assert parse_manual_scene_lines(text) == ["Line one", "Line two", "Line three"]


def test_parse_manual_scene_lines_normalizes_crlf():
    assert parse_manual_scene_lines("A\r\nB\rC") == ["A", "B", "C"]


def test_build_scene_plan_batch_from_lines_one_per_line():
    chapter = SimpleNamespace(
        id="ch-1",
        title="Manual Chapter",
        script_text="",
        summary="",
        target_duration_sec=120,
    )
    project = SimpleNamespace(
        visual_style="cinematic documentary",
        topic="test topic",
        preferred_image_provider=None,
        preferred_video_provider=None,
        no_narration=False,
    )
    lines = ["First beat narration.", "Second beat narration.", "Third beat."]
    batch = build_scene_plan_batch_from_lines(chapter, project, lines)
    assert batch["schema_id"] == "scene-plan-batch/v1"
    assert len(batch["scenes"]) == 3
    assert batch["scenes"][0]["narration_text"] == "First beat narration."
    assert batch["scenes"][1]["order_index"] == 1
    assert batch["scenes"][2]["narration_text"] == "Third beat."


def test_build_scene_plan_batch_from_lines_rejects_empty():
    chapter = SimpleNamespace(id="c", title="T", script_text="", summary="", target_duration_sec=None)
    project = SimpleNamespace(
        visual_style="doc",
        topic="t",
        preferred_image_provider=None,
        preferred_video_provider=None,
        no_narration=False,
    )
    with pytest.raises(ValueError, match="MANUAL_SCENE_LINES_REQUIRED"):
        build_scene_plan_batch_from_lines(chapter, project, [])


def test_build_scene_plan_batch_from_lines_rejects_too_many():
    chapter = SimpleNamespace(id="c", title="T", script_text="", summary="", target_duration_sec=None)
    project = SimpleNamespace(
        visual_style="doc",
        topic="t",
        preferred_image_provider=None,
        preferred_video_provider=None,
        no_narration=False,
    )
    lines = [f"line {i}" for i in range(MAX_MANUAL_SCENE_LINES + 1)]
    with pytest.raises(ValueError, match="MANUAL_SCENE_LINE_LIMIT"):
        build_scene_plan_batch_from_lines(chapter, project, lines)
