"""Paragraph-beat counting for chapter scripts when target scenes per chapter is set."""

from director_api.services.phase2 import script_scene_beat_paragraph_count


def test_script_scene_beat_paragraph_count_empty() -> None:
    assert script_scene_beat_paragraph_count("") == 0
    assert script_scene_beat_paragraph_count("   ") == 0


def test_script_scene_beat_paragraph_count_single() -> None:
    assert script_scene_beat_paragraph_count("One block of text.") == 1


def test_script_scene_beat_paragraph_count_blank_lines() -> None:
    s = "First beat here.\n\nSecond beat.\n\nThird."
    assert script_scene_beat_paragraph_count(s) == 3


def test_script_scene_beat_paragraph_count_crlf() -> None:
    s = "A\r\n\r\nB"
    assert script_scene_beat_paragraph_count(s) == 2
