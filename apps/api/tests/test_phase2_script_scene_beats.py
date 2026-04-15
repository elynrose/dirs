"""Paragraph-beat counting for chapter scripts when target scenes per chapter is set."""

from director_api.services.phase2 import (
    deterministic_chapter_script_emergency,
    script_scene_beat_paragraph_count,
)


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


def test_deterministic_chapter_script_emergency_non_empty() -> None:
    s = deterministic_chapter_script_emergency(
        chapter_title="Cold open",
        chapter_summary="We introduce the stakes and the place.",
        project_topic="A documentary about resilience.",
        min_words=120,
        target_scenes_per_chapter=0,
    )
    assert len(s.strip()) > 80
    assert len(s.split()) >= 120


def test_deterministic_chapter_script_emergency_scene_beats() -> None:
    tsp = 4
    s = deterministic_chapter_script_emergency(
        chapter_title="Act II",
        chapter_summary="",
        project_topic="Urban renewal",
        min_words=200,
        target_scenes_per_chapter=tsp,
    )
    assert script_scene_beat_paragraph_count(s) == tsp
    assert len(s.split()) >= 200
