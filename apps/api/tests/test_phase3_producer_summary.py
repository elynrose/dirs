from types import SimpleNamespace

from director_api.services.phase3 import (
    build_scene_plan_batch,
    chapter_eligible_for_scene_extend,
    chapter_eligible_for_scene_planning,
    is_producer_only_chapter_summary_for_vo,
    resolve_chapter_narration_tts_body,
)


def test_detects_outline_beat_placeholder():
    s = 'Outline beat for «The bible story of Elijah» — target ~240s narration.'
    assert is_producer_only_chapter_summary_for_vo(s) is True


def test_detects_producer_note_prefix():
    s = "Producer note (do not use as narration): Expand «Ch1» into full spoken script; target ~120s."
    assert is_producer_only_chapter_summary_for_vo(s) is True


def test_real_script_not_producer_only():
    s = "Elijah challenged the prophets on Mount Carmel while the people watched in silence."
    assert is_producer_only_chapter_summary_for_vo(s) is False


def test_chapter_not_eligible_on_outline_summary_only():
    ch = SimpleNamespace(
        script_text="",
        summary='Outline beat for «T» — target ~240s narration.',
    )
    assert chapter_eligible_for_scene_planning(ch) is False


def test_extend_eligible_from_existing_scene_text_without_chapter_script():
    ch = SimpleNamespace(
        script_text="",
        summary="",
        scenes=[
            SimpleNamespace(
                narration_text="First beat narration with enough substance to continue the story.",
                purpose="Open the scene",
            )
        ],
    )
    assert chapter_eligible_for_scene_extend(ch) is True


def test_extend_not_eligible_when_scenes_too_thin_and_no_script():
    ch = SimpleNamespace(
        script_text="",
        summary="",
        scenes=[SimpleNamespace(narration_text="Hi", purpose="x")],
    )
    assert chapter_eligible_for_scene_extend(ch) is False


def test_resolve_tts_prefers_script_when_scenes_are_placeholder():
    ch = SimpleNamespace(
        script_text="Real narration here for the listener. " * 5,
        summary="",
    )
    sc = SimpleNamespace(narration_text='Outline beat for «T» — target ~240s narration.')
    body = resolve_chapter_narration_tts_body(ch, [sc])
    assert body and "Real narration" in body


def test_build_scene_plan_rejects_outline_summary_without_script():
    ch = SimpleNamespace(
        script_text="",
        summary='Outline beat for «T» — target ~240s narration.',
        target_duration_sec=120,
        title="Ch1",
    )
    project = SimpleNamespace(
        visual_style="cinematic documentary",
        topic="t",
        preferred_image_provider=None,
        preferred_video_provider=None,
    )
    try:
        build_scene_plan_batch(ch, project)
    except ValueError as e:
        assert "CHAPTER_SCRIPT_REQUIRED" in str(e)
    else:
        raise AssertionError("expected ValueError")
