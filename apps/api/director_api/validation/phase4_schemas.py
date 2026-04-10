"""Normalize critic agent output (Phase 4)."""

from __future__ import annotations

from typing import Any

SCENE_DIMENSION_KEYS: tuple[str, ...] = (
    "script_alignment",
    "visual_coherence",
    "factual_confidence",
    "continuity_consistency",
    "emotional_fit",
    "pacing_usefulness",
    "technical_quality",
)

CHAPTER_DIMENSION_KEYS: tuple[str, ...] = (
    "narrative_arc",
    "chapter_transitions",
    "runtime_fit",
    "repetition_control",
    "source_coverage",
)


def _clamp01(x: Any, *, invalid_fallback: float = 0.5) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        fb = float(invalid_fallback)
        return max(0.0, min(1.0, fb))
    return max(0.0, min(1.0, v))


def normalize_scene_dimensions(
    raw: dict[str, Any] | None,
    *,
    missing_default: float = 0.6,
    invalid_fallback: float = 0.5,
) -> dict[str, float]:
    out: dict[str, float] = {}
    src = raw if isinstance(raw, dict) else {}
    md = float(missing_default)
    md = max(0.0, min(1.0, md))
    for k in SCENE_DIMENSION_KEYS:
        if k not in src:
            out[k] = md
        else:
            out[k] = _clamp01(src.get(k), invalid_fallback=invalid_fallback)
    return out


def normalize_chapter_dimensions(
    raw: dict[str, Any] | None,
    *,
    missing_default: float = 0.6,
    invalid_fallback: float = 0.5,
) -> dict[str, float]:
    out: dict[str, float] = {}
    src = raw if isinstance(raw, dict) else {}
    md = float(missing_default)
    md = max(0.0, min(1.0, md))
    for k in CHAPTER_DIMENSION_KEYS:
        if k not in src:
            out[k] = md
        else:
            out[k] = _clamp01(src.get(k), invalid_fallback=invalid_fallback)
    return out


def normalize_issues(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "GENERIC")[:64]
        sev = str(item.get("severity") or "medium").lower()[:16]
        if sev not in ("low", "medium", "high"):
            sev = "medium"
        msg = str(item.get("message") or "")[:8000]
        refs = item.get("refs")
        if refs is not None and not isinstance(refs, (dict, list)):
            refs = None
        out.append({"code": code, "severity": sev, "message": msg, "refs": refs})
    return out


def normalize_recommendations(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x)[:2000] for x in raw if x is not None][:50]
