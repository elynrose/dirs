from types import SimpleNamespace

from director_api.services.phase3 import build_scene_plan_batch, scene_plan_refine_context


def test_scene_plan_splits_single_long_block_into_multiple_scenes():
    chapter = SimpleNamespace(
        script_text=(
            "Ahab rises to power and consolidates influence across the kingdom. "
            "His policies deepen social fracture and normalize fear among ordinary families. "
            "Prophetic voices challenge the court and call the nation back to covenant faithfulness. "
            "Pressure builds as regional conflict and internal corruption grow side by side. "
            "Mercy is still present in the story, but it appears in tension with violence and pride. "
            "The chapter closes with consequences that set up the next movement in the narrative."
        ),
        summary="",
        target_duration_sec=180,
        title="Test Chapter",
    )
    project = SimpleNamespace(
        visual_style="cinematic documentary",
        topic="Ahab and mercy",
        preferred_image_provider=None,
        preferred_video_provider=None,
    )
    out = build_scene_plan_batch(chapter, project)
    assert out["schema_id"] == "scene-plan-batch/v1"
    assert len(out["scenes"]) >= 2


def test_scene_plan_refine_context_clip_and_count_band():
    chapter = SimpleNamespace(
        script_text=" ".join(["word"] * 260),
        summary="",
        target_duration_sec=0,
        title="T",
    )
    settings = SimpleNamespace(scene_clip_duration_sec=10)
    ctx = scene_plan_refine_context(chapter, settings)
    assert ctx["scene_clip_duration_sec"] == 10
    assert ctx["word_count"] == 260
    assert ctx["estimated_narration_sec"] == max(5, int(round(260 / 130.0 * 60)))
    assert ctx["suggested_scene_count"] == max(1, min(48, int(round(ctx["estimated_narration_sec"] / 10.0))))
    assert ctx["scene_count_min"] <= ctx["suggested_scene_count"] <= ctx["scene_count_max"]


def test_single_block_splits_when_chapter_target_short_but_script_long():
    """Regression: target_duration_sec under 90s must not force a single scene for long VO."""
    parts = [f"Beat {i} explains one thread of the story with care. " for i in range(20)]
    script = "".join(parts).strip()
    chapter = SimpleNamespace(
        script_text=script,
        summary="",
        target_duration_sec=60,
        title="T",
    )
    project = SimpleNamespace(
        visual_style="cinematic documentary",
        topic="doc",
        preferred_image_provider=None,
        preferred_video_provider=None,
    )
    out = build_scene_plan_batch(chapter, project)
    assert len(out["scenes"]) >= 2


def test_min_scenes_expands_seed():
    chapter = SimpleNamespace(
        script_text="Alpha. Bravo. Charlie. Delta. Echo. Foxtrot.",
        summary="",
        target_duration_sec=30,
        title="T",
    )
    project = SimpleNamespace(
        visual_style="cinematic documentary",
        topic="doc",
        preferred_image_provider=None,
        preferred_video_provider=None,
    )
    out = build_scene_plan_batch(chapter, project, min_scenes=5)
    assert len(out["scenes"]) >= 5


def test_scene_plan_splits_run_on_without_sentence_endings():
    """Scripts pasted without .!? still become multiple beats via word chunking."""
    script = " ".join([f"segment{i}" for i in range(140)])
    chapter = SimpleNamespace(
        script_text=script,
        summary="",
        target_duration_sec=200,
        title="T",
    )
    project = SimpleNamespace(
        visual_style="cinematic documentary",
        topic="doc",
        preferred_image_provider=None,
        preferred_video_provider=None,
    )
    out = build_scene_plan_batch(chapter, project)
    assert len(out["scenes"]) >= 2


def test_scene_plan_splits_on_semicolons():
    body = "; ".join([f"Clause {i} adds another beat to the narration" for i in range(6)])
    chapter = SimpleNamespace(
        script_text=body,
        summary="",
        target_duration_sec=120,
        title="T",
    )
    project = SimpleNamespace(
        visual_style="cinematic documentary",
        topic="doc",
        preferred_image_provider=None,
        preferred_video_provider=None,
    )
    out = build_scene_plan_batch(chapter, project)
    assert len(out["scenes"]) >= 2


def test_medium_script_never_collapses_seed_to_one_scene():
    """Regression: ~40+ words should yield suggested>=2 so auto mode seed beats LLM single-scene collapse."""
    script = (
        "This chapter introduces the central conflict and names the stakes for everyone watching at home. "
        "It explains why the moment matters and what could change if the story moves forward with courage."
    )
    chapter = SimpleNamespace(
        script_text=script,
        summary="",
        target_duration_sec=120,
        title="T",
    )
    project = SimpleNamespace(
        visual_style="cinematic documentary",
        topic="doc",
        preferred_image_provider=None,
        preferred_video_provider=None,
    )
    out = build_scene_plan_batch(chapter, project, scene_clip_duration_sec=10)
    assert len(out["scenes"]) >= 2


def test_scene_plan_refine_context_workspace_minimum_scenes_floor():
    chapter = SimpleNamespace(
        script_text=" ".join(["word"] * 260),
        summary="",
        target_duration_sec=0,
        title="T",
    )
    settings = SimpleNamespace(scene_clip_duration_sec=10, scene_plan_target_scenes_per_chapter=7)
    ctx = scene_plan_refine_context(chapter, settings)
    assert ctx["scene_count_min"] == 7
    assert ctx["scene_count_max"] == 48
    assert ctx["suggested_scene_count"] >= 7
    assert ctx["suggested_scene_count"] >= ctx["scene_count_min"]
