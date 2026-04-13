"""Tests for [bracket] narration visual hints."""

from director_api.services.narration_bracket_visual import (
    base_image_prompt_from_scene_fields,
    extract_bracket_phrases,
    video_text_prompt_from_scene_fields,
)


def test_extract_multiple_brackets():
    t = "There [mermaids] were rare until one [reappeared on the shore]."
    assert extract_bracket_phrases(t) == ["mermaids", "reappeared on the shore"]


def test_extract_ignores_empty():
    assert extract_bracket_phrases("no [ ] brackets") == []


def test_base_image_prefers_brackets_over_package():
    pp = {"image_prompt": "A generic stock photo of waves"}
    p, used, phrases = base_image_prompt_from_scene_fields(
        narration_text="See [the temple] at dawn.",
        prompt_package_json=pp,
        image_prompt_override=None,
    )
    assert used is True
    assert "the temple" in p
    assert "generic stock" not in p.lower()


def test_base_image_override_wins():
    p, used, phrases = base_image_prompt_from_scene_fields(
        narration_text="See [ignored].",
        prompt_package_json={"image_prompt": "x"},
        image_prompt_override="  Manual override  ",
    )
    assert used is False
    assert phrases == []
    assert p.strip() == "Manual override"


def test_video_uses_brackets_when_no_video_prompt():
    t = video_text_prompt_from_scene_fields(
        narration_text="The [volcano] glowed.",
        purpose=None,
        visual_type=None,
        prompt_package_json={},
        video_prompt_override=None,
    )
    assert "volcano" in t.lower()
    assert "motion" in t.lower() or "documentary" in t.lower()
