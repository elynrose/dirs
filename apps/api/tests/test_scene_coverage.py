"""Scene coverage helpers (multi-clip vs VO budget)."""

from director_api.services.scene_coverage import coverage_visual_slots_needed, pick_coverage_payload


def test_coverage_visual_slots_needed() -> None:
    assert coverage_visual_slots_needed(budget_sec=12.0, clip_sec=5.0) == 3
    assert coverage_visual_slots_needed(budget_sec=5.0, clip_sec=10.0) == 1
    assert coverage_visual_slots_needed(budget_sec=25.0, clip_sec=10.0, max_slots=2) == 2


def test_pick_coverage_payload_keys() -> None:
    p = pick_coverage_payload(take_index=3)
    assert "image_prompt_override" in p and "video_prompt_override" in p
    assert "exclude_character_bible" in p
    assert isinstance(p["exclude_character_bible"], bool)
