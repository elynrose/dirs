"""Deterministic continuity checks and critic helpers (Phase 4)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import Asset, Chapter, Scene
from director_api.validation.phase4_schemas import (
    normalize_chapter_dimensions,
    normalize_issues,
    normalize_recommendations,
    normalize_scene_dimensions,
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"\W+", (text or "").lower()) if len(t) > 3}


def continuity_findings_for_scene(scene: Scene, siblings: list[Scene]) -> list[dict[str, Any]]:
    """Per-scene continuity signals vs other scenes in the same chapter."""
    findings: list[dict[str, Any]] = []
    tags: list[str] = []
    if isinstance(scene.continuity_tags_json, list):
        tags = [str(t) for t in scene.continuity_tags_json if t is not None][:64]
    all_tags: Counter[str] = Counter()
    for s in siblings:
        if isinstance(s.continuity_tags_json, list):
            for t in s.continuity_tags_json:
                all_tags[str(t)] += 1
    for t in tags:
        if all_tags[t] >= 3:
            findings.append(
                {
                    "code": "RECURRING_TAG",
                    "severity": "low",
                    "message": f"Tag {t!r} appears across many scenes; verify visual variety.",
                    "refs": {"tags": [t], "scene_ids": [str(scene.id)]},
                }
            )
            break
    ordered = sorted(siblings, key=lambda x: x.order_index)
    idx = next((i for i, s in enumerate(ordered) if s.id == scene.id), -1)
    if idx > 0:
        prev = ordered[idx - 1]
        a, b = _tokens(prev.narration_text or ""), _tokens(scene.narration_text or "")
        if len(a) >= 8 and len(b) >= 8:
            inter = len(a & b)
            union = len(a | b) or 1
            if inter / union > 0.45:
                findings.append(
                    {
                        "code": "NARRATION_OVERLAP",
                        "severity": "medium",
                        "message": "Adjacent scenes share many narration tokens; check redundancy.",
                        "refs": {
                            "scene_ids": [str(prev.id), str(scene.id)],
                        },
                    }
                )
    if not (scene.narration_text or "").strip():
        findings.append(
            {
                "code": "EMPTY_NARRATION",
                "severity": "high",
                "message": "Scene has little or no narration text for script alignment.",
                "refs": {"scene_ids": [str(scene.id)]},
            }
        )
    return findings


def chapter_continuity_rollup(scenes: list[Scene]) -> dict[str, Any]:
    """Chapter-level tag and pacing rollup."""
    tag_counts: Counter[str] = Counter()
    total_dur = 0
    for s in scenes:
        total_dur += int(s.planned_duration_sec or 0)
        if isinstance(s.continuity_tags_json, list):
            for t in s.continuity_tags_json:
                tag_counts[str(t)] += 1
    top_tags = [t for t, c in tag_counts.most_common(12)]
    return {
        "scene_count": len(scenes),
        "planned_duration_sec_total": total_dur,
        "distinct_tags": len(tag_counts),
        "top_tags": top_tags,
    }


def merge_heuristic_scene_critique(
    *,
    continuity_issues: list[dict[str, Any]],
    has_approved_image: bool,
    dimensions_llm: dict[str, Any] | None,
    recommendations_llm: list[str] | None,
    threshold: float,
    missing_dimension_default: float,
    dimension_invalid_fallback: float,
) -> tuple[float, bool, dict[str, float], list[dict[str, Any]], list[str]]:
    dims = normalize_scene_dimensions(
        dimensions_llm,
        missing_default=missing_dimension_default,
        invalid_fallback=dimension_invalid_fallback,
    )
    high_continuity = sum(1 for x in continuity_issues if x.get("severity") == "high")
    if high_continuity:
        dims["continuity_consistency"] = min(dims["continuity_consistency"], 0.35)
        dims["script_alignment"] = min(dims["script_alignment"], 0.45)
    elif continuity_issues:
        dims["continuity_consistency"] = min(dims["continuity_consistency"], 0.55)
    if not has_approved_image:
        dims["technical_quality"] = min(dims["technical_quality"], 0.5)
        dims["visual_coherence"] = min(dims["visual_coherence"], 0.45)
    score = sum(dims.values()) / max(len(dims), 1)
    issues = normalize_issues(continuity_issues)
    recs = normalize_recommendations(recommendations_llm)
    if continuity_issues and not recs:
        recs = ["Resolve continuity findings, then re-run critique."]
    passed = score >= threshold and not any(i["severity"] == "high" for i in issues)
    return score, passed, dims, issues, recs


def chapter_aggregate_from_scenes(
    scenes: list[Scene],
    *,
    target_duration_sec: int | None,
    chapter_dims_llm: dict[str, Any] | None,
    continuity_rollup: dict[str, Any],
    threshold_ratio: float,
    min_aggregate_score: float,
    missing_dimension_default: float,
    dimension_invalid_fallback: float,
) -> tuple[float, bool, dict[str, float], list[dict[str, Any]], list[str]]:
    dims = normalize_chapter_dimensions(
        chapter_dims_llm,
        missing_default=missing_dimension_default,
        invalid_fallback=dimension_invalid_fallback,
    )
    n = len(scenes)
    if n == 0:
        return 0.0, False, dims, [{"code": "NO_SCENES", "severity": "high", "message": "No scenes in chapter.", "refs": {}}], []
    passed_scenes = sum(1 for s in scenes if s.critic_passed is True)
    ratio = passed_scenes / n
    if ratio < threshold_ratio:
        dims["narrative_arc"] = min(dims["narrative_arc"], 0.45)
    tgt = target_duration_sec
    total = int(continuity_rollup.get("planned_duration_sec_total") or 0)
    if tgt and total and abs(total - tgt) / max(tgt, 1) > 0.35:
        dims["runtime_fit"] = min(dims["runtime_fit"], 0.4)
    score = sum(dims.values()) / max(len(dims), 1)
    issues: list[dict[str, Any]] = []
    if ratio < threshold_ratio:
        issues.append(
            {
                "code": "SCENE_PASS_RATIO",
                "severity": "high",
                "message": (
                    f"Only {passed_scenes}/{n} scenes marked critic_passed; need ≥{threshold_ratio:.0%} "
                    "(chapter min. scene pass ratio from workspace critic settings)."
                ),
                "refs": {"scene_ids": [str(s.id) for s in scenes]},
            }
        )
    if continuity_rollup.get("distinct_tags", 0) == 0 and n > 2:
        issues.append(
            {
                "code": "NO_CONTINUITY_TAGS",
                "severity": "low",
                "message": "No continuity tags across scenes; optional for stylistic tracking.",
                "refs": {},
            }
        )
    if score < min_aggregate_score:
        issues.append(
            {
                "code": "CHAPTER_AGGREGATE_SCORE",
                "severity": "high",
                "message": (
                    f"Chapter aggregate score {score:.2f} is below required {min_aggregate_score:.2f} "
                    "(mean of chapter dimension scores; threshold from workspace critic settings)."
                ),
                "refs": {},
            }
        )
    passed = ratio >= threshold_ratio and score >= min_aggregate_score and not any(i["severity"] == "high" for i in issues)
    recs: list[str] = []
    if not passed:
        recs.append("Re-run scene critiques or adjust scenes before Phase 5 handoff.")
    return score, passed, dims, normalize_issues(issues), recs


def build_scene_critique_llm_payload(db: Session, sc: Scene) -> dict[str, Any]:
    """Payload for scene critic LLM (shared by sequential and parallel OpenAI Agents paths)."""
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise ValueError("chapter not found")
    siblings = list(
        db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
    )
    cont = continuity_findings_for_scene(sc, siblings)
    assets = list(db.scalars(select(Asset).where(Asset.scene_id == sc.id)).all())
    has_ok_image = any(a.asset_type == "image" and a.approved_at is not None for a in assets)
    return {
        "purpose": sc.purpose,
        "narration_excerpt": (sc.narration_text or "")[:4000],
        "visual_type": sc.visual_type,
        "planned_duration_sec": sc.planned_duration_sec,
        "continuity_tags": sc.continuity_tags_json,
        "has_approved_image": has_ok_image,
        "continuity_flags": cont,
    }


def build_chapter_critique_llm_payload(db: Session, ch: Chapter) -> dict[str, Any]:
    scenes = list(
        db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
    )
    rollup = chapter_continuity_rollup(scenes)
    summaries = [
        {
            "order_index": s.order_index,
            "critic_score": s.critic_score,
            "critic_passed": s.critic_passed,
            "purpose": (s.purpose or "")[:200],
        }
        for s in scenes
    ]
    return {
        "chapter_title": ch.title,
        "target_duration_sec": ch.target_duration_sec,
        "continuity_rollup": rollup,
        "scene_summaries": summaries,
    }
