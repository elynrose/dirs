"""Helpers for auto-generating extra scene clips so visual runway matches narration (multi-clip beats)."""

from __future__ import annotations

import math
import random
from typing import Any

# Randomized shot grammar: appended to provider prompts (overrides) so extra takes differ from the hero.
_COVERAGE_VARIANTS: list[dict[str, Any]] = [
    {
        "image_suffix": "Wide establishing shot, same location and continuity as the scene.",
        "video_suffix": "Slow cinematic pan, wide establishing shot, gentle camera movement.",
        "exclude_character_bible": False,
    },
    {
        "image_suffix": "Medium shot, alternate framing, same scene and wardrobe.",
        "video_suffix": "Subtle handheld movement, medium shot, shallow depth of field.",
        "exclude_character_bible": False,
    },
    {
        "image_suffix": "Low angle shot, dramatic perspective, same setting.",
        "video_suffix": "Slow tilt up, low angle, same environment.",
        "exclude_character_bible": False,
    },
    {
        "image_suffix": "Environmental detail, props, hands, or location texture — no main characters in frame.",
        "video_suffix": "Slow push-in on environmental details and textures, no people, b-roll.",
        "exclude_character_bible": True,
    },
    {
        "image_suffix": "Over-the-shoulder style framing toward the scene subject, same continuity.",
        "video_suffix": "Slow drift, over-the-shoulder toward the focal point, cinematic.",
        "exclude_character_bible": False,
    },
    {
        "image_suffix": "Insert shot, close detail, same lighting and palette.",
        "video_suffix": "Macro-style slow move on a key detail, shallow focus.",
        "exclude_character_bible": False,
    },
]


def coverage_visual_slots_needed(*, budget_sec: float, clip_sec: float, max_slots: int = 8) -> int:
    """How many ~clip_sec segments are needed to cover ``budget_sec`` (VO + tail)."""
    b = max(0.25, float(budget_sec))
    c = max(1.0, float(clip_sec))
    return max(1, min(max_slots, int(math.ceil(b / c))))


def pick_coverage_payload(take_index: int) -> dict[str, Any]:
    """Build job payload fields for one extra coverage take (randomized angle / optional no-hero)."""
    rng = random.Random(take_index * 7919 + 104729)
    v = dict(rng.choice(_COVERAGE_VARIANTS))
    if v.get("exclude_character_bible") is not bool:
        v["exclude_character_bible"] = bool(rng.random() < 0.35)
    return {
        "image_prompt_override": str(v["image_suffix"])[:4000],
        "video_prompt_override": str(v["video_suffix"])[:3000],
        "exclude_character_bible": bool(v.get("exclude_character_bible")),
    }
