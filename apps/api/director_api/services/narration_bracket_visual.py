"""Derive image/video visual prompts from ``[bracketed]`` hints in scene narration."""

from __future__ import annotations

import re
from typing import Any

from director_api.services.research_service import sanitize_jsonb_text

# Non-nested segments: [like this] — avoids greedy crossing.
_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")


def extract_bracket_phrases(narration_text: str | None) -> list[str]:
    """Return non-empty inner strings for each ``[...]`` segment in order."""
    if not narration_text or not str(narration_text).strip():
        return []
    out: list[str] = []
    for m in _BRACKET_RE.finditer(str(narration_text)):
        inner = (m.group(1) or "").strip()
        if inner:
            out.append(inner)
    return out


def compose_bracket_visual_prompt(
    phrases: list[str],
    *,
    narration_full: str | None = None,
    for_video_motion_hint: bool = False,
) -> str:
    """Turn user bracket hints into one abstract visual prompt (style layers added later by the worker)."""
    if not phrases:
        return ""
    # Cap to avoid huge narrations with many brackets
    use = phrases[:16]
    joined = "; ".join(use)
    if for_video_motion_hint:
        base = (
            f"Cinematic documentary shot: {joined}. "
            "Subtle natural motion or slow camera move; one coherent beat."
        )
    else:
        base = (
            f"A single photoreal documentary still — abstract tableau: {joined}. "
            "One cohesive composition; clear focal subject and setting implied by the hints."
        )
    return sanitize_jsonb_text(base, 4000)


def _append_image_prompt_suffix(prompt: str, image_prompt_suffix: str | None) -> str:
    suffix = (image_prompt_suffix or "").strip()
    if not suffix:
        return prompt
    base = (prompt or "").strip()
    combined = f"{base}\n\n{suffix}" if base else suffix
    return sanitize_jsonb_text(combined, 4000)


def base_image_prompt_from_scene_fields(
    *,
    narration_text: str | None,
    prompt_package_json: dict[str, Any] | None,
    image_prompt_override: str | None,
    visual_style_effective: str | None = None,
    image_prompt_suffix: str | None = None,
) -> tuple[str, bool, list[str]]:
    """Return ``(prompt, used_bracket_hints, bracket_phrases)`` before character/style prefixes.

    Precedence: explicit job override → ``[bracket]`` hints in narration → ``image_prompt`` in package → narration excerpt.
    ``visual_style_effective`` is accepted for API compatibility; callers typically append style after character bible.
    """
    del visual_style_effective  # applied by phase-3 worker after character prefix

    if isinstance(image_prompt_override, str) and image_prompt_override.strip():
        p = sanitize_jsonb_text(image_prompt_override.strip(), 4000)
        return _append_image_prompt_suffix(p, image_prompt_suffix), False, []

    pp = prompt_package_json if isinstance(prompt_package_json, dict) else {}
    narr = narration_text or ""
    phrases = extract_bracket_phrases(narr)
    if phrases:
        p = compose_bracket_visual_prompt(phrases, narration_full=narr, for_video_motion_hint=False)
        return _append_image_prompt_suffix(p, image_prompt_suffix), True, phrases

    prompt = pp.get("image_prompt") if isinstance(pp.get("image_prompt"), str) else None
    if not prompt:
        prompt = narr[:1200]
    p = sanitize_jsonb_text(str(prompt), 4000)
    return _append_image_prompt_suffix(p, image_prompt_suffix), False, []


def _append_video_prompt_suffix(prompt: str, video_prompt_suffix: str | None) -> str:
    suffix = (video_prompt_suffix or "").strip()
    if not suffix:
        return prompt
    base = (prompt or "").strip()
    combined = f"{base}\n\n{suffix}" if base else suffix
    return sanitize_jsonb_text(combined, 3000)


def video_text_prompt_from_scene_fields(
    *,
    narration_text: str | None,
    purpose: str | None,
    visual_type: str | None,
    prompt_package_json: dict[str, Any] | None,
    video_prompt_override: str | None,
    visual_style_effective: str | None = None,
    video_prompt_suffix: str | None = None,
) -> str:
    """Resolve text for video models: override → ``video_prompt`` → ``[bracket]`` hints → VO/purpose."""
    if isinstance(video_prompt_override, str) and video_prompt_override.strip():
        return _append_video_prompt_suffix(
            sanitize_jsonb_text(video_prompt_override.strip(), 3000),
            video_prompt_suffix,
        )
    pp = prompt_package_json if isinstance(prompt_package_json, dict) else {}
    vp = pp.get("video_prompt") if isinstance(pp.get("video_prompt"), str) else None
    if vp and str(vp).strip():
        return _append_video_prompt_suffix(sanitize_jsonb_text(str(vp).strip(), 3000), video_prompt_suffix)
    narr = narration_text or ""
    phrases = extract_bracket_phrases(narr)
    if phrases:
        out = sanitize_jsonb_text(
            compose_bracket_visual_prompt(phrases, narration_full=narr, for_video_motion_hint=True),
            3000,
        )
        return _append_video_prompt_suffix(out, video_prompt_suffix)
    base = narr or purpose or visual_type or "cinematic documentary scene"
    out = sanitize_jsonb_text(str(base)[:3000], 3000)
    vs = (visual_style_effective or "").strip()
    if vs and vs[:100] not in (out[-min(len(out), 800) :] if out else ""):
        room = max(0, 3000 - len(out) - 24)
        if room > 80:
            out = sanitize_jsonb_text(f"{out}\n\nVisual style: {vs[:room]}", 3000)
    return _append_video_prompt_suffix(out, video_prompt_suffix)


def append_video_character_dialogue_to_prompt(
    base: str,
    *,
    include_spoken_dialogue_in_video_prompt: bool,
    video_character_dialogue: str | None,
) -> str:
    """When the project opts in and the scene has dialogue text, append a model-friendly ``saying: \"…\"`` fragment."""
    if not include_spoken_dialogue_in_video_prompt:
        return base
    raw = video_character_dialogue if isinstance(video_character_dialogue, str) else ""
    line = sanitize_jsonb_text(raw.strip(), 800)
    if not line:
        return base
    inner = line.replace('"', "'")
    suffix = f' saying: "{inner}"'
    cap = 3000
    if len(base) + len(suffix) <= cap:
        return sanitize_jsonb_text(base + suffix, cap)
    room = max(0, cap - len(suffix))
    trimmed = base[:room] if room else ""
    return sanitize_jsonb_text(trimmed + suffix, cap)


def maybe_prepend_topic_setting_anchor(
    prompt: str,
    topic: str | None,
    *,
    max_total: int = 4000,
) -> str:
    """Append a short project-topic anchor when it is not already present (legacy phase-3 helper)."""
    p = sanitize_jsonb_text(str(prompt or "").strip(), max_total)
    t = sanitize_jsonb_text(str(topic or "").strip(), 200)
    if not p or not t:
        return p
    if t.lower() in p.lower():
        return p
    suffix = f" | Setting: {t}"
    room = max(0, max_total - len(suffix))
    return sanitize_jsonb_text(p[:room] + suffix, max_total)
