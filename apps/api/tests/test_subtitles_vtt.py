from types import SimpleNamespace
from uuid import uuid4

from director_api.services.subtitles_vtt import assemble_project_subtitle_markdown, script_to_webvtt


def test_assemble_subtitles_prefers_scene_narration_in_order():
    cid = uuid4()
    ch = SimpleNamespace(id=cid, title="Act I", script_text="CHAPTER ONLY")
    sc_a = SimpleNamespace(
        chapter_id=cid,
        order_index=0,
        purpose="Opening",
        planned_duration_sec=12,
        narration_text="Line from scene A.",
    )
    sc_b = SimpleNamespace(
        chapter_id=cid,
        order_index=1,
        purpose="",
        planned_duration_sec=8,
        narration_text="Line from scene B.",
    )
    body, total = assemble_project_subtitle_markdown([ch], [sc_a, sc_b])
    assert "Line from scene A." in body
    assert "Line from scene B." in body
    assert "Act I · Opening" in body
    assert "CHAPTER ONLY" not in body
    assert total == 20.0


def test_assemble_subtitles_falls_back_to_chapter_scripts():
    cid = uuid4()
    ch = SimpleNamespace(id=cid, title="Act I", script_text="Fallback chapter body.")
    sc = SimpleNamespace(
        chapter_id=cid,
        order_index=0,
        purpose=None,
        planned_duration_sec=5,
        narration_text="   ",
    )
    body, total = assemble_project_subtitle_markdown([ch], [sc])
    assert "Fallback chapter body." in body
    assert total == 5.0


def test_webvtt_contains_cues():
    vtt = script_to_webvtt("Hello world.\n\nSecond paragraph here.", total_sec=10.0)
    assert vtt.startswith("WEBVTT")
    assert "Hello world." in vtt
    assert "Second paragraph" in vtt
    assert "-->" in vtt


def test_job_quota_category():
    from director_api.services.job_quota import COMPILE_TYPES, MEDIA_TYPES, TEXT_TYPES

    assert "rough_cut" in COMPILE_TYPES
    assert "fine_cut" in COMPILE_TYPES
    assert "export" in COMPILE_TYPES
    assert "scene_generate_image" in MEDIA_TYPES
    assert "scene_extend" in MEDIA_TYPES
    assert "research_run" in TEXT_TYPES
