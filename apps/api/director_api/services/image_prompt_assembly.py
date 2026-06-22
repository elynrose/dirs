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
    style_already_stripped: bool = False,
) -> str:
    """Rewrite into Flux-friendly labeled sections for any visual preset."""
    from director_api.services.flux_structured_prompt import structure_flux_scene_prompt

    vs = (vis_style or "").strip()
    p = (prompt or "").strip()
    if vs and not style_already_stripped:
        p = strip_redundant_visual_style_clauses(p, vs)
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
    style_already_stripped: bool = False,
) -> str:
    """Structured Flux/WAN video prompt for any visual preset."""
    from director_api.services.flux_structured_prompt import structure_flux_scene_prompt

    vs = (vis_style or "").strip()
    p = (prompt or "").strip()
    if vs and not style_already_stripped:
        p = strip_redundant_visual_style_clauses(p, vs)
    return structure_flux_scene_prompt(
        p,
        visual_preset_id=visual_preset_id,
        visual_style_resolved=vs or None,
        mood=mood,
        for_video=True,
        max_total=max_total,
    )


_NO_PEOPLE_PHRASES = (
    "no people",
    "no humans",
    "no human",
    "no figures",
    "no person",
    "no characters",
    "without people",
    "without humans",
    "empty street",
    "empty room",
    "empty courtyard",
)

_FRAMING_TAGS_NO_HUMAN_HEAD = {"[ECU]", "[INSERT]", "[BROLL]"}

_FRAMING_SAFETY_POS = (
    "Composition safety: keep the subject's full head and shoulders inside the frame with "
    "breathing room above the crown; the top of the head must sit well below the upper edge "
    "of the image; do not crop the face."
)


def prompt_leading_shot_tag(prompt: str | None) -> str | None:
    if not prompt:
        return None
    s = prompt.lstrip()
    if not s.startswith("["):
        return None
    end = s.find("]")
    if end <= 1 or end > 12:
        return None
    return s[: end + 1].upper()


def prompt_declares_no_humans(prompt: str | None) -> bool:
    """True when the image prompt is explicitly people-free."""
    if not prompt:
        return False
    if prompt_leading_shot_tag(prompt) in _FRAMING_TAGS_NO_HUMAN_HEAD:
        return True
    lowered = prompt.lower()
    return any(phrase in lowered for phrase in _NO_PEOPLE_PHRASES)


def should_append_framing_safety_positive(prompt: str, *, character_prefix_injected: bool) -> bool:
    if prompt_declares_no_humans(prompt):
        return False
    return bool(character_prefix_injected)


def assemble_scene_still_image_prompt(
    db: Any,
    scene: Any,
    project: Any,
    settings: Any,
    prompt: str,
    *,
    exclude_character_bible: bool = False,
    automation_character_prefix: str | None = None,
    append_framing_safety: bool = False,
    append_era_anchor: bool = False,
    mood: str | None = None,
) -> str:
    """Shared Flux/Comfy still prompt recipe for image jobs, previews, and WAN auto-stills."""
    return _assemble_scene_generative_prompt(
        db,
        scene,
        project,
        settings,
        prompt,
        for_video=False,
        exclude_character_bible=exclude_character_bible,
        automation_character_prefix=automation_character_prefix,
        append_framing_safety=append_framing_safety,
        append_era_anchor=append_era_anchor,
        mood=mood,
    )


def assemble_scene_video_prompt(
    db: Any,
    scene: Any,
    project: Any,
    settings: Any,
    prompt: str,
    *,
    exclude_character_bible: bool = False,
    automation_character_prefix: str | None = None,
    append_era_anchor: bool = False,
    mood: str | None = None,
) -> str:
    """Shared Flux/WAN video prompt recipe for video jobs and resolved-prompt preview."""
    return _assemble_scene_generative_prompt(
        db,
        scene,
        project,
        settings,
        prompt,
        for_video=True,
        exclude_character_bible=exclude_character_bible,
        automation_character_prefix=automation_character_prefix,
        append_framing_safety=False,
        append_era_anchor=append_era_anchor,
        mood=mood,
    )


def _assemble_scene_generative_prompt(
    db: Any,
    scene: Any,
    project: Any,
    settings: Any,
    prompt: str,
    *,
    for_video: bool,
    exclude_character_bible: bool = False,
    automation_character_prefix: str | None = None,
    append_framing_safety: bool = False,
    append_era_anchor: bool = False,
    mood: str | None = None,
) -> str:
    from director_api.services import phase3 as phase3_svc
    from director_api.services.camera_perspective import inject_camera_perspective_into_prompt
    from director_api.services.flux_structured_prompt import (
        inject_characters_into_labeled_prompt,
        is_labeled_flux_prompt,
    )
    from director_api.services.narration_bracket_visual import maybe_prepend_topic_setting_anchor
    from director_api.services.character_prompt import prompt_already_has_character_prefix
    from director_api.style_presets import effective_visual_style

    max_total = 3000 if for_video else 4000
    vis_style = effective_visual_style(getattr(project, "visual_style", None), settings)
    out = str(prompt).strip()
    if vis_style:
        out = strip_redundant_visual_style_clauses(out, vis_style)

    labeled_prompt = is_labeled_flux_prompt(out)
    character_prefix_injected = False

    if not exclude_character_bible:
        prefix = ""
        if automation_character_prefix:
            prefix = str(automation_character_prefix)[:2000]
        elif not prompt_declares_no_humans(out):
            prefix = character_consistency_block_for_image(
                db,
                project.id,
                scene_text=scene_text_for_character_match(db, scene),
                base_prompt=out,
                max_chars=2000,
            )
        if prefix and not prompt_already_has_character_prefix(out, prefix):
            if labeled_prompt:
                out = inject_characters_into_labeled_prompt(out, prefix, max_total=max_total)
            elif for_video:
                room = max(400, max_total - len(prefix) - 3)
                out = f"{prefix}\n\n{str(out)[:room]}"
            else:
                room = max(400, max_total - len(out) - 3)
                out = f"{str(out)[:room]}\n\n{prefix}"
            character_prefix_injected = True
        elif prefix:
            character_prefix_injected = True

    if not labeled_prompt:
        out = inject_camera_perspective_into_prompt(
            out,
            scene_key=str(scene.id),
            order_index=int(scene.order_index),
            for_video=for_video,
            max_total=max_total,
        )
        out = maybe_prepend_topic_setting_anchor(
            out, getattr(project, "topic", None), max_total=max_total
        )

    if append_framing_safety and should_append_framing_safety_positive(
        out, character_prefix_injected=character_prefix_injected
    ):
        room_fr = max(0, max_total - len(out) - 2)
        if room_fr > len(_FRAMING_SAFETY_POS):
            out = f"{out}\n\n{_FRAMING_SAFETY_POS}"

    if append_era_anchor:
        from director_api.db.models import Chapter

        try:
            chapter = db.get(Chapter, scene.chapter_id) if getattr(scene, "chapter_id", None) else None
        except Exception:  # noqa: BLE001
            chapter = None
        era_anchor = _scene_era_anchor_clause(scene, chapter, project)
        if era_anchor:
            room_ea = max(0, max_total - len(out) - 2)
            if room_ea > len(era_anchor):
                out = f"{out}\n\n{era_anchor}"

    pid_vis = phase3_svc.resolve_visual_preset_id_for_project(project, settings)
    resolved_mood = mood if mood is not None else (getattr(scene, "purpose", None) or "")[:240] or None
    if for_video:
        return polish_scene_video_prompt(
            out,
            vis_style=vis_style,
            visual_preset_id=pid_vis,
            max_total=max_total,
            mood=resolved_mood,
            style_already_stripped=True,
        )
    return polish_scene_image_prompt(
        out,
        vis_style=vis_style,
        visual_preset_id=pid_vis,
        max_total=max_total,
        mood=resolved_mood,
        style_already_stripped=True,
    )


def _scene_era_anchor_clause(
    scene: Any,
    chapter: Any | None,
    project: Any | None,
    *,
    max_chars: int = 160,
) -> str:
    pieces: list[str] = []
    ch_title = (chapter.title or "").strip() if chapter is not None else ""
    pj_title = (project.title or "").strip() if project is not None else ""
    if ch_title and ch_title.lower() != pj_title.lower():
        pieces.append(ch_title)
    if pj_title:
        pieces.append(pj_title)
    if not pieces:
        return ""
    seen: set[str] = set()
    uniq: list[str] = []
    for p in pieces:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    clause = " — ".join(uniq)
    if len(clause) > max_chars:
        clause = clause[: max_chars - 1].rstrip(" ,;:.—-") + "…"
    return f"Set in: {clause}."


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
        db, project_id, scene_text=scene_text, base_prompt=base_prompt, max_chars=max_chars
    )
    if not full:
        return ""
    base = (base_prompt or "").strip()
    if use_short_when_scene_detailed and len(base) >= 400:
        short = character_short_prefix_for_scene(
            db,
            project_id,
            scene_text=scene_text,
            base_prompt=base_prompt,
            max_chars=min(800, max_chars),
        )
        if short:
            return short
    return full
