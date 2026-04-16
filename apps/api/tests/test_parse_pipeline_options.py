"""Regression: hands-off (unattended) must not default to critique depth."""

from director_api.services.agent_resume import (
    apply_pipeline_speed_for_persist,
    normalize_pipeline_options_for_persist,
    parse_pipeline_options,
)


def test_unattended_missing_through_is_full_video():
    cont, through, unattended = parse_pipeline_options({"unattended": True, "continue_from_existing": True})
    assert cont is True
    assert unattended is True
    assert through == "full_video"


def test_unattended_with_critique_coerced_to_full_video():
    cont, through, unattended = parse_pipeline_options(
        {"unattended": True, "through": "critique", "continue_from_existing": False}
    )
    assert cont is False
    assert unattended is True
    assert through == "full_video"


def test_unattended_explicit_chapters_preserved():
    _, through, unattended = parse_pipeline_options({"unattended": True, "through": "chapters"})
    assert unattended is True
    assert through == "chapters"


def test_attended_default_critique():
    _, through, unattended = parse_pipeline_options({"continue_from_existing": True})
    assert unattended is False
    assert through == "critique"


def test_normalize_persist_writes_effective_through():
    out = normalize_pipeline_options_for_persist({"unattended": True, "through": "critique"})
    assert out["through"] == "full_video"
    assert out["unattended"] is True


def test_pipeline_speed_demo_fast_expands_and_strips_key():
    out = normalize_pipeline_options_for_persist(
        {
            "continue_from_existing": True,
            "through": "full_video",
            "pipeline_speed": "demo_fast",
        }
    )
    assert "pipeline_speed" not in out
    assert out["_applied_pipeline_speed"] == "demo_fast"
    assert out["auto_generate_scene_videos"] is False
    assert out["auto_generate_scene_images"] is True
    assert out["min_scene_images"] == 1
    assert out["min_scene_videos"] == 1


def test_pipeline_speed_demo_fast_user_min_images_wins():
    out = apply_pipeline_speed_for_persist(
        {"pipeline_speed": "demo_fast", "min_scene_images": 4, "through": "full_video"},
    )
    assert out["min_scene_images"] == 4
    assert out["auto_generate_scene_videos"] is False


def test_pipeline_speed_production_heavy():
    out = normalize_pipeline_options_for_persist(
        {"continue_from_existing": True, "through": "full_video", "pipeline_speed": "production_heavy"},
    )
    assert out["_applied_pipeline_speed"] == "production_heavy"
    assert out["min_scene_images"] == 2
    assert out["min_scene_videos"] == 2
    assert out["auto_generate_scene_videos"] is True


def test_pipeline_speed_unknown_passthrough():
    raw = {"through": "full_video", "pipeline_speed": "ludicrous_mode"}
    out = normalize_pipeline_options_for_persist(raw)
    assert out.get("pipeline_speed") == "ludicrous_mode"
    assert "_applied_pipeline_speed" not in out
