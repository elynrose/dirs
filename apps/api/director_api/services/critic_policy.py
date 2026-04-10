"""Per-project critic thresholds (Phase 4-R05) merged with env defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EffectiveCriticPolicy:
    pass_threshold: float
    max_revision_cycles_per_scene: int
    chapter_min_scene_pass_ratio: float
    chapter_pass_score_threshold: float
    missing_dimension_default: float
    dimension_invalid_fallback: float


def effective_policy(project: Any, settings: Any) -> EffectiveCriticPolicy:
    pol: dict[str, Any] = (
        project.critic_policy_json if project and isinstance(project.critic_policy_json, dict) else {}
    )

    def _f(key: str, default: float) -> float:
        v = pol.get(key)
        if v is None:
            return float(default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return float(default)

    def _i(key: str, default: int) -> int:
        v = pol.get(key)
        if v is None:
            return int(default)
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return int(default)

    mds = _f("missing_dimension_default", settings.critic_missing_dimension_default)
    return EffectiveCriticPolicy(
        pass_threshold=_f("pass_threshold", settings.critic_pass_threshold),
        max_revision_cycles_per_scene=_i(
            "max_revision_cycles_per_scene",
            settings.critic_max_revision_cycles_per_scene,
        ),
        chapter_min_scene_pass_ratio=_f(
            "chapter_min_scene_pass_ratio",
            settings.chapter_min_scene_pass_ratio,
        ),
        chapter_pass_score_threshold=_f(
            "chapter_pass_score_threshold",
            settings.chapter_pass_score_threshold,
        ),
        missing_dimension_default=mds,
        dimension_invalid_fallback=_f("dimension_invalid_fallback", mds),
    )
