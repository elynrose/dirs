"""Dedupe and place visual-style / character blocks for scene image generation."""

from __future__ import annotations

import re
from typing import Any

from director_api.services.research_service import sanitize_jsonb_text

_ILLUSTRATIVE_PRESET_IDS = frozenset({"three_d_animation", "hand_drawn_2d", "flat_infographic"})

_PRESET_STYLE_MARKERS: dict[str, str] = {
    "three_d_animation": "stylized 3d animated film still",
    "hand_drawn_2d": "hand-drawn 2d animation still",
    "flat_infographic": "flat vector infographic still",
}

_VISUAL_STYLE_SUFFIX_RE = re.compile(r"\n+\s*Visual style:\s*", re.IGNORECASE)

# One compact line — Flux/Comfy still workflows often zero-out negative conditioning.
_THREE_D_MEDIUM_LOCK = (
    "3D CGI render with volumetric depth, rounded sculptural character models, and soft global illumination — "
    "strictly NOT 2D hand-drawn cel animation, flat cartoon illustration, inked line art, or classic Disney 2D sketch style."
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def prompt_contains_visual_style(prompt: str, vis_style: str) -> bool:
    """True when the prompt already includes this visual-style block (anywhere)."""
    p = _norm(prompt)
    vs = _norm(vis_style)
    if not p or not vs:
        return False
    if len(vs) >= 32 and vs[: min(100, len(vs))] in p:
        return True
    if len(vs) >= 48 and vs[:48] in p:
        return True
    for marker in _PRESET_STYLE_MARKERS.values():
        if marker in vs and marker in p:
            return True
    if "photoreal live-action documentary still" in vs and "photoreal live-action documentary still" in p:
        return True
    return False


def strip_redundant_visual_style_clauses(prompt: str, vis_style: str) -> str:
    """Remove duplicate trailing ``Visual style:`` blocks and repeated leading style paragraphs."""
    vs = (vis_style or "").strip()
    p = (prompt or "").strip()
    if not p:
        return p
    vs_norm = _norm(vs)

    while True:
        idx = p.lower().rfind("visual style:")
        if idx < 0:
            break
        tail_norm = _norm(p[idx:])
        if vs_norm and (
            vs_norm[:50] in tail_norm
            or any(m in tail_norm for m in _PRESET_STYLE_MARKERS.values())
        ):
            p = p[:idx].rstrip()
        else:
            break

    if vs_norm:
        parts = re.split(r"\n\s*\n", p, maxsplit=3)
        if len(parts) >= 2 and vs_norm[:60] in _norm(parts[0]) and vs_norm[:60] in _norm(parts[1]):
            if len(_norm(parts[0])) >= 40:
                p = "\n\n".join(parts[1:]).strip()

    return p


def apply_visual_style_once(
    prompt: str,
    vis_style: str,
    *,
    visual_preset_id: str | None = None,
    max_total: int = 4000,
) -> str:
    """Ensure the visual-style clause appears at most once (leading for illustrative presets)."""
    vs = (vis_style or "").strip()
    p = strip_redundant_visual_style_clauses((prompt or "").strip(), vs)
    if not vs:
        return sanitize_jsonb_text(p, max_total)
    if prompt_contains_visual_style(p, vs):
        return sanitize_jsonb_text(p, max_total)

    pid = (visual_preset_id or "").strip().lower()
    if pid in _ILLUSTRATIVE_PRESET_IDS:
        lead = vs[:900]
        combined = f"{lead}\n\n{p}" if p else lead
        return sanitize_jsonb_text(combined, max_total)

    room = max(0, max_total - len(p) - 16)
    if room > 80:
        return sanitize_jsonb_text(f"{p}\n\nVisual style: {vs[:room]}", max_total)
    return sanitize_jsonb_text(p, max_total)


def append_three_d_medium_lock(prompt: str, *, max_total: int = 4000) -> str:
    """Reinforce 3D-vs-2D medium on the positive prompt (needed when Flux has no negative CLIP node)."""
    p = (prompt or "").strip()
    lock = _THREE_D_MEDIUM_LOCK
    if not p:
        return sanitize_jsonb_text(lock, max_total)
    n = _norm(p)
    if "strictly not 2d hand-drawn cel" in n or "3d cgi render with volumetric depth" in n:
        return sanitize_jsonb_text(p, max_total)
    combined = f"{p}\n\n{lock}"
    return sanitize_jsonb_text(combined, max_total)


def compact_avoidance_clause_from_negative(negative: str, *, max_chars: int = 320) -> str:
    """Short 'Avoid in image:' line for Flux workflows without a negative prompt node."""
    raw = re.sub(r"\s+", " ", (negative or "").strip())
    if not raw:
        return ""
    # Prefer style-opposition tokens; drop generic quality tokens to save space.
    drop = frozenset(
        {
            "text",
            "watermark",
            "logo",
            "subtitles",
            "ui",
            "blurry",
            "low resolution",
            "deformed anatomy",
            "extra limbs",
            "oversaturated",
            "collage",
            "split screen",
            "cropped head",
            "cropped face",
        }
    )
    parts = [x.strip() for x in raw.split(",") if x.strip()]
    kept: list[str] = []
    for part in parts:
        key = part.lower().split("(")[0].strip()
        if any(key == d or key.startswith(d) for d in drop):
            continue
        kept.append(part)
    clause = ", ".join(kept) if kept else raw[:max_chars]
    return clause[:max_chars]


def polish_scene_image_prompt(
    prompt: str,
    *,
    vis_style: str | None,
    visual_preset_id: str | None,
    max_total: int = 4000,
    mood: str | None = None,
) -> str:
    """Rewrite into Flux-friendly labeled sections for any visual preset."""
    from director_api.services.flux_structured_prompt import structure_flux_scene_prompt

    vs = (vis_style or "").strip()
    p = strip_redundant_visual_style_clauses((prompt or "").strip(), vs)
    return structure_flux_scene_prompt(
        p,
        visual_preset_id=visual_preset_id,
        visual_style_resolved=vs or None,
        mood=mood,
        for_video=False,
        max_total=max_total,
    )


def polish_scene_video_prompt(
    prompt: str,
    *,
    vis_style: str | None,
    visual_preset_id: str | None,
    max_total: int = 3000,
    mood: str | None = None,
) -> str:
    """Structured Flux/WAN video prompt for any visual preset."""
    from director_api.services.flux_structured_prompt import structure_flux_scene_prompt

    vs = (vis_style or "").strip()
    p = strip_redundant_visual_style_clauses((prompt or "").strip(), vs)
    return structure_flux_scene_prompt(
        p,
        visual_preset_id=visual_preset_id,
        visual_style_resolved=vs or None,
        mood=mood,
        for_video=True,
        max_total=max_total,
    )


def scene_text_for_character_match(db: Any, scene: Any) -> str:
    """Narration + purpose + chapter title for per-scene bible filtering."""
    from director_api.db.models import Chapter

    parts: list[str] = []
    n = (getattr(scene, "narration_text", None) or "").strip()
    if n:
        parts.append(n)
    p = (getattr(scene, "purpose", None) or "").strip()
    if p:
        parts.append(p)
    pp = getattr(scene, "prompt_package_json", None)
    if isinstance(pp, dict):
        ip = pp.get("image_prompt")
        if isinstance(ip, str) and ip.strip():
            parts.append(ip.strip()[:2000])
    try:
        ch = db.get(Chapter, scene.chapter_id) if getattr(scene, "chapter_id", None) else None
    except Exception:  # noqa: BLE001
        ch = None
    if ch is not None:
        t = (ch.title or "").strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def character_consistency_block_for_image(
    db: Any,
    project_id: Any,
    *,
    scene_text: str,
    base_prompt: str,
    max_chars: int = 2000,
    use_short_when_scene_detailed: bool = True,
) -> str:
    """Full or compact character bible for one scene (avoids repeating long looks in dense prompts)."""
    from director_api.services.character_prompt import (
        character_consistency_prefix_for_scene,
        character_short_prefix_for_scene,
    )

    full = character_consistency_prefix_for_scene(
        db, project_id, scene_text=scene_text, max_chars=max_chars
    )
    if not full:
        return ""
    base = (base_prompt or "").strip()
    if use_short_when_scene_detailed and len(base) >= 400:
        short = character_short_prefix_for_scene(
            db, project_id, scene_text=scene_text, max_chars=min(800, max_chars)
        )
        if short:
            return short
    return full
