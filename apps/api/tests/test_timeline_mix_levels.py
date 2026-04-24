from director_api.timeline_mix_levels import mix_music_volume_from_timeline, mix_narration_volume_from_timeline


def test_mix_music_volume_zero_not_replaced_by_default() -> None:
    assert mix_music_volume_from_timeline({"mix_music_volume": 0}) == 0.0


def test_mix_music_volume_missing_uses_default() -> None:
    assert mix_music_volume_from_timeline({}) == 0.28


def test_mix_narration_volume_zero_not_replaced() -> None:
    assert mix_narration_volume_from_timeline({"mix_narration_volume": 0}) == 0.0


def test_mix_narration_invalid_falls_back() -> None:
    assert mix_narration_volume_from_timeline({"mix_narration_volume": "x"}) == 1.0
