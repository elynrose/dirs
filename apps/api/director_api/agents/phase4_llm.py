"""Optional LLM-backed scene/chapter critic (structured JSON)."""

from __future__ import annotations

import json
from typing import Any

from director_api.agents.phase2_llm import _chat_json_object_ex
from director_api.config import Settings
from director_api.services.llm_prompt_runtime import get_llm_prompt_text
from director_api.services.research_service import sanitize_jsonb_text


def _chat_json_object(
    settings: Settings,
    *,
    system: str,
    user: str,
    service_type: str,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    data, _err = _chat_json_object_ex(
        settings,
        system=system,
        user=user,
        service_type=service_type,
        usage_sink=usage_sink,
        temperature=0.25,
    )
    return data


def critique_scene_llm(
    payload: dict[str, Any],
    *,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, list[str] | None]:
    """Returns (dimensions dict, recommendations list) or (None, None)."""
    sys = get_llm_prompt_text("phase4_scene_critique_json")
    user = json.dumps(payload, ensure_ascii=False)[:24000]
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase4_scene_critique", usage_sink=usage_sink
    )
    if not out or not isinstance(out.get("dimensions"), dict):
        return None, None
    recs = out.get("recommendations")
    if isinstance(recs, list):
        return out["dimensions"], [str(x) for x in recs if x is not None][:12]
    return out["dimensions"], []


def revise_scene_narration_llm(
    *,
    purpose: str | None,
    narration_text: str | None,
    recommendations: list[str],
    settings: Settings,
    narration_style: str | None = None,
    usage_sink: list[dict[str, Any]] | None = None,
) -> str | None:
    """Return revised narration_text or None to use deterministic fallback."""
    sys = get_llm_prompt_text("phase4_scene_narration_revise_base")
    if (narration_style or "").strip():
        sys += " Overall voice brief: " + (narration_style.strip()[:1200])
    user = json.dumps(
        {
            "purpose": (purpose or "")[:2000],
            "narration_text": (narration_text or "")[:12000],
            "recommendations": recommendations[:12],
        },
        ensure_ascii=False,
    )[:24000]
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase4_scene_narration_revise", usage_sink=usage_sink
    )
    if not out or not isinstance(out.get("narration_text"), str):
        return None
    return str(out["narration_text"]).strip() or None


def revise_chapter_scenes_batch_llm(
    *,
    chapter_title: str,
    target_duration_sec: int | None,
    issues_json: Any,
    recommendations_json: Any,
    scenes_payload: list[dict[str, Any]],
    settings: Settings,
    narration_style: str | None = None,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[int, str] | None:
    """
    After chapter critic fails, propose updated narration_text per scene (by order_index).
    Returns {order_index: narration_text} or None.
    """
    issues = issues_json if isinstance(issues_json, list) else []
    recs: list[str] = []
    if isinstance(recommendations_json, list):
        recs = [str(x) for x in recommendations_json if x is not None][:16]
    sys = get_llm_prompt_text("phase4_chapter_batch_revise_base")
    if (narration_style or "").strip():
        sys += " Voice brief: " + (narration_style.strip()[:1200])
    user = json.dumps(
        {
            "chapter_title": (chapter_title or "")[:500],
            "target_duration_sec": target_duration_sec,
            "critic_issues": issues[:24],
            "critic_recommendations": recs,
            "scenes": scenes_payload[:48],
        },
        ensure_ascii=False,
    )[:24000]
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase4_chapter_batch_revise", usage_sink=usage_sink
    )
    if not out or not isinstance(out.get("updates"), list):
        return None
    m: dict[int, str] = {}
    for it in out["updates"]:
        if not isinstance(it, dict):
            continue
        try:
            oi = int(it.get("order_index", -999))
        except (TypeError, ValueError):
            continue
        if oi < 0:
            continue
        nt = it.get("narration_text")
        if not isinstance(nt, str):
            continue
        t = nt.strip()
        if not t:
            continue
        m[oi] = sanitize_jsonb_text(t, 12_000)
    return m or None


def critique_chapter_llm(
    payload: dict[str, Any],
    *,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, list[str] | None]:
    sys = get_llm_prompt_text("phase4_chapter_critique_json")
    user = json.dumps(payload, ensure_ascii=False)[:24000]
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase4_chapter_critique", usage_sink=usage_sink
    )
    if not out or not isinstance(out.get("dimensions"), dict):
        return None, None
    recs = out.get("recommendations")
    if isinstance(recs, list):
        return out["dimensions"], [str(x) for x in recs if x is not None][:12]
    return out["dimensions"], []


def story_research_consistency_review(
    payload: dict[str, Any],
    *,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Single project-level review: scripted story vs research dossier.
    Returns parsed JSON or None if the model/key is unavailable.
    """
    sys = get_llm_prompt_text("phase4_story_research_review")
    user = json.dumps(payload, ensure_ascii=False)[:24000]
    out = _chat_json_object(
        settings,
        system=sys,
        user=user,
        service_type="phase4_story_research_review",
        usage_sink=usage_sink,
    )
    if not out or not isinstance(out.get("summary"), str):
        return None
    try:
        score = float(out.get("alignment_score", 0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    aligned = bool(out.get("aligned_with_research"))
    issues = out.get("issues")
    if not isinstance(issues, list):
        issues = []
    recs = out.get("recommendations")
    if not isinstance(recs, list):
        recs = []
    return {
        "alignment_score": score,
        "aligned_with_research": aligned,
        "summary": str(out["summary"]).strip()[:2000],
        "issues": issues[:32],
        "recommendations": [str(x) for x in recs if x is not None][:12],
    }
