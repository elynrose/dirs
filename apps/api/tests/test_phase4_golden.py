"""Golden-style unit checks for Phase 4 metrics (no DB)."""

from types import SimpleNamespace

from director_api.config import get_settings
from director_api.services import phase4 as phase4_svc


def test_chapter_aggregate_all_passed():
    settings = get_settings()
    scenes = [
        SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            critic_passed=True,
            order_index=0,
            planned_duration_sec=60,
            continuity_tags_json=None,
        ),
        SimpleNamespace(
            id="00000000-0000-0000-0000-000000000002",
            critic_passed=True,
            order_index=1,
            planned_duration_sec=60,
            continuity_tags_json=None,
        ),
    ]
    rollup = phase4_svc.chapter_continuity_rollup(scenes)  # type: ignore[arg-type]
    score, passed, _dims, issues, _recs = phase4_svc.chapter_aggregate_from_scenes(
        scenes,  # type: ignore[arg-type]
        target_duration_sec=120,
        chapter_dims_llm=None,
        continuity_rollup=rollup,
        threshold_ratio=float(settings.chapter_min_scene_pass_ratio),
        min_aggregate_score=float(settings.chapter_pass_score_threshold),
        missing_dimension_default=float(settings.critic_missing_dimension_default),
        dimension_invalid_fallback=float(settings.critic_missing_dimension_default),
    )
    assert score > 0
    assert passed is True
    assert issues == []


def test_chapter_aggregate_blocked_low_ratio():
    settings = get_settings()
    scenes = [
        SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            critic_passed=False,
            order_index=0,
            planned_duration_sec=60,
            continuity_tags_json=None,
        ),
        SimpleNamespace(
            id="00000000-0000-0000-0000-000000000002",
            critic_passed=True,
            order_index=1,
            planned_duration_sec=60,
            continuity_tags_json=None,
        ),
    ]
    rollup = phase4_svc.chapter_continuity_rollup(scenes)  # type: ignore[arg-type]
    _score, passed, _dims, issues, _recs = phase4_svc.chapter_aggregate_from_scenes(
        scenes,  # type: ignore[arg-type]
        target_duration_sec=120,
        chapter_dims_llm=None,
        continuity_rollup=rollup,
        threshold_ratio=float(settings.chapter_min_scene_pass_ratio),
        min_aggregate_score=float(settings.chapter_pass_score_threshold),
        missing_dimension_default=float(settings.critic_missing_dimension_default),
        dimension_invalid_fallback=float(settings.critic_missing_dimension_default),
    )
    assert passed is False
    assert any(i.get("code") == "SCENE_PASS_RATIO" for i in issues)


def test_chapter_aggregate_blocked_low_aggregate_score():
    settings = get_settings()
    scenes = [
        SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            critic_passed=True,
            order_index=0,
            planned_duration_sec=60,
            continuity_tags_json=["a"],
        ),
        SimpleNamespace(
            id="00000000-0000-0000-0000-000000000002",
            critic_passed=True,
            order_index=1,
            planned_duration_sec=60,
            continuity_tags_json=["b"],
        ),
    ]
    rollup = phase4_svc.chapter_continuity_rollup(scenes)  # type: ignore[arg-type]
    low_dims = {k: 0.2 for k in ("narrative_arc", "chapter_transitions", "runtime_fit", "repetition_control", "source_coverage")}
    _score, passed, _dims, issues, _recs = phase4_svc.chapter_aggregate_from_scenes(
        scenes,  # type: ignore[arg-type]
        target_duration_sec=120,
        chapter_dims_llm=low_dims,
        continuity_rollup=rollup,
        threshold_ratio=float(settings.chapter_min_scene_pass_ratio),
        min_aggregate_score=float(settings.chapter_pass_score_threshold),
        missing_dimension_default=float(settings.critic_missing_dimension_default),
        dimension_invalid_fallback=float(settings.critic_missing_dimension_default),
    )
    assert passed is False
    assert any(i.get("code") == "CHAPTER_AGGREGATE_SCORE" for i in issues)


def test_merge_heuristic_improves_with_llm_dims():
    settings = get_settings()
    continuity = []
    score, passed, dims, issues, recs = phase4_svc.merge_heuristic_scene_critique(
        continuity_issues=continuity,
        has_approved_image=True,
        dimensions_llm={"script_alignment": 0.9, "visual_coherence": 0.9},
        recommendations_llm=["Tighten opening"],
        threshold=0.55,
        missing_dimension_default=float(settings.critic_missing_dimension_default),
        dimension_invalid_fallback=float(settings.critic_missing_dimension_default),
    )
    assert score >= 0.55
    assert passed is True
    assert "script_alignment" in dims
