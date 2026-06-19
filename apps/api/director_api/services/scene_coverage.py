"""Helpers for auto-generating extra scene clips so visual runway matches narration (multi-clip beats)."""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import Asset, Chapter, Scene
from director_api.services.scene_timeline_duration import (
    effective_scene_visual_budget_sec,
    scene_vo_tail_padding_sec_from_settings,
)

# Randomized shot grammar: appended to provider prompts (overrides) so extra takes differ from the hero.
_COVERAGE_VARIANTS: list[dict[str, Any]] = [
    {
        "image_suffix": "Wide establishing shot, same location and continuity as the scene.",
        "video_suffix": "Slow cinematic pan, wide establishing shot, gentle camera movement.",
        "exclude_character_bible": False,
    },
    {
        "image_suffix": "High-angle shot looking down on the scene, same location and continuity.",
        "video_suffix": "Slow crane down or tilt, high angle, same environment.",
        "exclude_character_bible": False,
    },
    {
        "image_suffix": "View from behind the subject toward what they face, same wardrobe and era.",
        "video_suffix": "Slow follow from behind, same subject and setting.",
        "exclude_character_bible": False,
    },
    {
        "image_suffix": "Side profile three-quarter framing, same scene and wardrobe.",
        "video_suffix": "Lateral truck along the profile, shallow depth of field.",
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


def _scene_succeeded_visual_count(db: Session, scene_id: UUID) -> int:
    assets = list(db.scalars(select(Asset).where(Asset.scene_id == scene_id)).all())
    return sum(
        1
        for a in assets
        if str(a.status or "") == "succeeded" and str(a.asset_type or "").lower() in ("image", "video")
    )


def project_scene_coverage_counts(
    db: Session,
    project_id: UUID,
    *,
    storage_root: str | Path | None,
    clip_sec: float,
    tail_padding_sec: float,
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 120.0,
) -> tuple[int, int, int, int]:
    """Return ``(scene_count, scenes_met, slots_have, slots_need)`` for pipeline status.

    A scene is *met* when succeeded image+video count is at least the slots needed for its VO budget.
    """
    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    root = Path(storage_root).resolve() if storage_root else None
    scenes_met = 0
    slots_have = 0
    slots_need = 0
    for sc in scenes:
        budget = effective_scene_visual_budget_sec(
            db,
            scene=sc,
            project_id=project_id,
            base_clip_sec=float(clip_sec),
            storage_root=root or Path("."),
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
            tail_padding_sec=float(tail_padding_sec),
        )
        need = coverage_visual_slots_needed(budget_sec=budget, clip_sec=float(clip_sec))
        have = _scene_succeeded_visual_count(db, sc.id)
        slots_need += need
        slots_have += min(have, need)
        if have >= need:
            scenes_met += 1
    return len(scenes), scenes_met, slots_have, slots_need


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
