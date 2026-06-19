"""Tests for pipeline fallback event helpers."""

from director_api.services.pipeline_fallback_events import (
    auto_videos_partial_failed_extra,
    visual_heal_event_fields,
)


def test_partial_failed_zero_generated_adds_summary():
    extra = auto_videos_partial_failed_extra(generated=0, failed_scene_count=5)
    assert "failure_reason_summary" in extra
    assert "no scene videos" in extra["failure_reason_summary"].lower()


def test_partial_failed_some_generated_no_summary():
    extra = auto_videos_partial_failed_extra(generated=3, failed_scene_count=2)
    assert extra == {}


def test_visual_heal_event_fields():
    fields = visual_heal_event_fields(
        scene_id="abc-123",
        auto_generate_scene_images=False,
        auto_generate_scene_videos=True,
    )
    assert fields["scene_id"] == "abc-123"
    assert fields["heal_kind"] == "timeline_image"
    assert fields["auto_generate_scene_images"] is False
    assert fields["auto_generate_scene_videos"] is True
    assert "summary" in fields
