"""Scene VO tail padding and planned-duration helpers."""

from director_api.services.scene_timeline_duration import (
    DEFAULT_SCENE_VO_TAIL_PADDING_SEC,
    min_planned_duration_int_for_narration_sec,
)


def test_min_planned_covers_narration_plus_padding() -> None:
    assert min_planned_duration_int_for_narration_sec(10.0, tail_padding_sec=5.0) == 15
    assert min_planned_duration_int_for_narration_sec(10.2, tail_padding_sec=5.0) == 16
    assert min_planned_duration_int_for_narration_sec(0.0, tail_padding_sec=5.0) == 5


def test_custom_tail_padding() -> None:
    assert min_planned_duration_int_for_narration_sec(10.0, tail_padding_sec=3.0) == 13


def test_default_tail_padding_matches_config_default() -> None:
    assert DEFAULT_SCENE_VO_TAIL_PADDING_SEC == 1.5
    assert min_planned_duration_int_for_narration_sec(10.0) == 12  # 10 + 1.5 → ceil 12


def test_min_planned_clamped_to_schema_bounds() -> None:
    assert min_planned_duration_int_for_narration_sec(900.0, tail_padding_sec=5.0) == 600
    assert min_planned_duration_int_for_narration_sec(-1.0, tail_padding_sec=5.0) == 5  # negative treated as 0s VO + padding
