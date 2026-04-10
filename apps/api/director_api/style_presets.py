"""Named narration and visual presets for documentaries (LLM / image-prompt guidance)."""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any, Final

from sqlalchemy.orm import Session

# Stored on Project as "preset:<id>" when chosen from UI, or custom free text (no prefix).
PRESET_PREFIX: Final = "preset:"
USER_PREFIX: Final = "user:"

DEFAULT_NARRATION_PRESET = "narrative_documentary"
DEFAULT_VISUAL_PRESET = "cinematic_documentary"

# id -> (short label, voice brief for LLM phases).
_NARRATION: dict[str, tuple[str, str]] = {
    "narrative_documentary": (
        "Narrative documentary (story-first)",
        "Narrative documentary voice: tell a story, not a research report. Open with a strong hook and clear "
        "stakes; build scenes with turning points, cause-and-effect, and a through-line the listener can follow. "
        "Weave facts, names, and dates only where they advance the plot—avoid abstract survey pacing, "
        "literature-review framing, thesis-style abstracts, or stacks of hedging. Use vivid concrete sensory "
        "detail where it matters; dramatic contrasts and occasional refrains are welcome. Prefer confident "
        "declarative delivery; use rhetorical questions sparingly for tension. Third person for the narrative "
        "spine; direct address to the audience only when the script calls for it (e.g. a closing reflection). "
        "Frame human stakes to match the topic. Stay factual and broadcast-safe; no clickbait or invented "
        "quotations.",
    ),
    "investigative_journalism": (
        "Investigative / accountability",
        "Investigative documentary voice: precise, evidence-forward, and calm. Lead with what is known vs "
        "alleged; attribute claims; avoid sensationalism. Short, clear sentences; occasional dry irony is fine. "
        "Build tension through facts and sequence, not hype. Third person; stay broadcast-safe and factual.",
    ),
    "warm_human_interest": (
        "Warm human-interest",
        "Human-interest documentary voice: empathetic, intimate, and grounded. Center real people and moments; "
        "use concrete detail and restrained emotion. Avoid melodrama and cliché. Conversational but polished; "
        "third person unless a direct address beat fits. Factual and respectful.",
    ),
    "measured_academic": (
        "Measured / explanatory",
        "Explanatory documentary voice: clear, structured, and accessible without being dry. Define terms when "
        "needed; use analogies sparingly and only when they clarify. Prefer neutral authority over hype. "
        "Third person; logical flow between ideas. Stay factual; no invented experts or quotes.",
    ),
}

_VISUAL: dict[str, tuple[str, str, str]] = {
    "cinematic_documentary": (
        "Cinematic Documentary (Live-Action Feel)",
        "Live-action documentary cinematography: natural lighting, realistic textures, handheld perspective, shallow "
        "depth of field, subtle film grain, authentic environments, emotionally grounded storytelling, high-end cinema camera.",
        "in a cinematic documentary style with a live-action feel, natural lighting, realistic textures, handheld camera "
        "perspective, shallow depth of field, subtle film grain, authentic environmental details, and emotionally grounded "
        "storytelling, captured with a high-end cinema camera.",
    ),
    "archival_historical": (
        "Archival / Historical Stills",
        "Archival historical photographs: period-accurate detail, monochrome or sepia, grain, soft contrast, fading, "
        "aging artifacts (scratches, vignetting), timeless documentary composition.",
        "as an archival historical photograph, featuring period-accurate details, monochrome or sepia tones, visible film "
        "grain, soft contrast, slight fading, authentic aging artifacts such as scratches and vignetting, and a timeless "
        "documentary composition.",
    ),
    "aerial_epic": (
        "Aerial / Epic Landscape",
        "Dramatic aerial perspective over expansive landscapes: sweeping vistas, atmospheric depth, volumetric light, "
        "majestic scale, cinematic composition, ultra-high-resolution detail.",
        "captured from a dramatic aerial perspective, showcasing an expansive epic landscape with sweeping vistas, "
        "atmospheric depth, volumetric lighting, majestic scale, cinematic composition, and ultra-high-resolution detail.",
    ),
    "noir_dramatic": (
        "Noir / Dramatic Reenactment",
        "Classic film noir reenactment: high-contrast black-and-white, deep shadows, low-key lighting, silhouettes, "
        "haze, tense mysterious framing.",
        "in a classic film noir style with dramatic reenactment, high-contrast black-and-white tones, deep shadows, "
        "moody low-key lighting, sharp silhouettes, subtle haze, and a tense, mysterious atmosphere with cinematic framing.",
    ),
    "three_d_animation": (
        "Stylized 3D Animation",
        "Stylized 3D animated film look: rounded characters, expressive eyes, smooth surfaces, saturated colors, global "
        "illumination, subsurface scattering, gentle depth of field, warm family-friendly mood, ultra-detailed render.",
        "in a stylized 3D animated film aesthetic, featuring soft rounded character design, large expressive eyes, smooth "
        "surfaces, vibrant saturated colors, cinematic global illumination, subsurface scattering, gentle depth of field, "
        "and a warm, family-friendly atmosphere, rendered in ultra-detailed high quality.",
    ),
    "hand_drawn_2d": (
        "Hand-Drawn 2D",
        "Hand-drawn 2D animation: clean line art, expressive characters, soft shading, painterly or watercolor texture, "
        "warm storybook aesthetic.",
        "illustrated in a hand-drawn 2D animation style, featuring clean line art, expressive character design, soft shading, "
        "painterly or watercolor textures, and a warm, storybook-like aesthetic.",
    ),
    "flat_infographic": (
        "Flat / Infographic",
        "Flat infographic illustration: simplified geometry, minimal layout, solid colors, clear hierarchy, vector look, "
        "modern educational tone.",
        "in a clean flat infographic style, using simplified geometric shapes, minimalistic design, solid colors, clear "
        "visual hierarchy, vector-based graphics, and a modern educational aesthetic.",
    ),
    "sci_tech_cgi": (
        "Sci-Tech CGI",
        "High-tech sci-fi CGI: sleek futurism, metal and glass, holographic UI hints, neon accents, rim lighting, "
        "photorealistic detail.",
        "rendered in a high-tech sci-fi CGI style, featuring sleek futuristic design, metallic and glass materials, "
        "holographic interface elements, neon accents, dramatic rim lighting, and ultra-detailed photorealistic rendering.",
    ),
    "cinematic_historical_epic": (
        "Cinematic Historical Epic",
        "Historical epic cinematography: grand scale, period-accurate costumes and sets, golden-hour light, rich grade, "
        "sweeping compositions, majestic emotional atmosphere.",
        "in a cinematic historical epic style, featuring grand scale, period-accurate costumes and environments, dramatic "
        "golden-hour lighting, rich color grading, sweeping composition, and a majestic, emotionally powerful atmosphere "
        "reminiscent of large-scale period films.",
    ),
}


def narration_preset_ids() -> tuple[str, ...]:
    return tuple(_NARRATION.keys())


def visual_preset_ids() -> tuple[str, ...]:
    return tuple(_VISUAL.keys())


def is_valid_narration_preset(preset_id: str | None) -> bool:
    return isinstance(preset_id, str) and preset_id.strip() in _NARRATION


def sanitize_default_narration_style_ref(raw: Any) -> str | None:
    """Allow preset:<known id>, user:<uuid>, or empty."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith(PRESET_PREFIX):
        pid = s.split(":", 1)[1].strip()
        return f"{PRESET_PREFIX}{pid}" if is_valid_narration_preset(pid) else None
    if low.startswith(USER_PREFIX):
        rest = s.split(":", 1)[1].strip()
        try:
            u = uuid_mod.UUID(rest)
        except (ValueError, TypeError, AttributeError):
            return None
        return f"{USER_PREFIX}{u}"
    return None


def is_valid_visual_preset(preset_id: str | None) -> bool:
    return isinstance(preset_id, str) and preset_id.strip() in _VISUAL


def narration_label(preset_id: str) -> str:
    return _NARRATION.get(preset_id, (preset_id, ""))[0]


def visual_label(preset_id: str) -> str:
    return _VISUAL.get(preset_id, (preset_id, "", ""))[0]


def narration_prompt_for_preset(preset_id: str | None) -> str:
    pid = (preset_id or "").strip() or DEFAULT_NARRATION_PRESET
    row = _NARRATION.get(pid)
    if not row:
        row = _NARRATION[DEFAULT_NARRATION_PRESET]
    return row[1]


def _visual_row_base(preset_id: str) -> tuple[str, str, str]:
    return _VISUAL.get(preset_id) or _VISUAL[DEFAULT_VISUAL_PRESET]


def sanitize_visual_preset_overrides(raw: Any) -> dict[str, dict[str, str]]:
    """Keep only known preset ids and clamp string lengths for app_settings JSON."""
    if not isinstance(raw, dict):
        return {}
    allowed = set(visual_preset_ids())
    out: dict[str, dict[str, str]] = {}
    for pid, blob in raw.items():
        if not isinstance(pid, str):
            continue
        key = pid.strip()
        if key not in allowed:
            continue
        if not isinstance(blob, dict):
            continue
        row: dict[str, str] = {}
        for fld, mx in (("label", 500), ("description", 4000), ("prompt", 12000)):
            v = blob.get(fld)
            if isinstance(v, str) and v.strip():
                row[fld] = v.strip()[:mx]
        if row:
            out[key] = row
    return out


def visual_prompt_for_preset(preset_id: str | None, settings: Any | None = None) -> str:
    pid = (preset_id or "").strip() or DEFAULT_VISUAL_PRESET
    base = _visual_row_base(pid)[2]
    if settings is not None:
        ov = getattr(settings, "visual_preset_overrides", None) or {}
        if isinstance(ov, dict) and pid in ov and isinstance(ov[pid], dict):
            p = ov[pid].get("prompt")
            if isinstance(p, str) and p.strip():
                return p.strip()[:12000]
    return base


def visual_description_for_preset(preset_id: str | None, settings: Any | None = None) -> str:
    """Human description (for UI); merges workspace overrides when present."""
    pid = (preset_id or "").strip() or DEFAULT_VISUAL_PRESET
    base = _visual_row_base(pid)[1]
    if settings is not None:
        ov = getattr(settings, "visual_preset_overrides", None) or {}
        if isinstance(ov, dict) and pid in ov and isinstance(ov[pid], dict):
            d = ov[pid].get("description")
            if isinstance(d, str) and d.strip():
                return d.strip()[:4000]
    return base


def _strip_preset_ref(stored: str | None) -> tuple[str | None, str | None]:
    """Return (preset_id, None) if preset:id, else (None, custom_text)."""
    s = (stored or "").strip()
    if not s:
        return None, None
    if s.lower().startswith(PRESET_PREFIX):
        return s.split(":", 1)[1].strip(), None
    return None, s


def _parse_narration_stored(stored: str | None) -> tuple[str | None, str | None]:
    """Return (kind, value): kind is preset|user|custom|None; value is id, uuid str, or free text."""
    s = (stored or "").strip()
    if not s:
        return None, None
    low = s.lower()
    if low.startswith(PRESET_PREFIX):
        return "preset", s.split(":", 1)[1].strip()
    if low.startswith(USER_PREFIX):
        return "user", s.split(":", 1)[1].strip()
    return "custom", s


def _user_narration_prompt_text(db: Session | None, tenant_id: str | None, uuid_str: str) -> str | None:
    if db is None or not tenant_id or not (uuid_str or "").strip():
        return None
    try:
        uid = uuid_mod.UUID(str(uuid_str).strip())
    except (ValueError, TypeError, AttributeError):
        return None
    from director_api.db.models import UserNarrationStyle

    row = db.get(UserNarrationStyle, uid)
    if not row or row.tenant_id != tenant_id:
        return None
    t = (row.prompt_text or "").strip()
    return t[:12000] if t else None


def _resolve_narration_from_settings_ref(
    settings: Any,
    *,
    db: Session | None,
    tenant_id: str | None,
) -> str | None:
    ref = getattr(settings, "default_narration_style_ref", None)
    if not isinstance(ref, str) or not ref.strip():
        return None
    kind, val = _parse_narration_stored(ref.strip())
    if kind == "custom" and val:
        return val
    if kind == "preset" and val:
        return narration_prompt_for_preset(val)
    if kind == "user" and val:
        got = _user_narration_prompt_text(db, tenant_id, val)
        if got:
            return got
    return None


def effective_narration_style(
    stored: str | None,
    settings: Any,
    *,
    db: Session | None = None,
    tenant_id: str | None = None,
) -> str:
    """Expand preset:/user: ref, custom project text, workspace default ref, or narration_style_preset."""
    kind, val = _parse_narration_stored(stored)
    if kind == "custom" and val:
        return val
    if kind == "preset" and val:
        return narration_prompt_for_preset(val)
    if kind == "user" and val:
        got = _user_narration_prompt_text(db, tenant_id, val)
        if got:
            return got
    if kind is None:
        from_ref = _resolve_narration_from_settings_ref(settings, db=db, tenant_id=tenant_id)
        if from_ref:
            return from_ref
    sp = getattr(settings, "narration_style_preset", None)
    return narration_prompt_for_preset(sp if is_valid_narration_preset(sp) else None)


def effective_visual_style(stored: str | None, settings: Any) -> str:
    """Expand preset: id or custom project visual_style; else runtime preset from settings; merges overrides."""
    pid, custom = _strip_preset_ref(stored)
    if custom:
        return custom
    if pid:
        return visual_prompt_for_preset(pid, settings)
    sp = getattr(settings, "visual_style_preset", None)
    return visual_prompt_for_preset(sp if is_valid_visual_preset(sp) else None, settings)


def style_presets_public_payload() -> dict[str, Any]:
    """For GET /v1/settings/style-presets (labels, descriptions, prompts — base definitions)."""
    return {
        "narration_presets": [
            {"id": k, "label": v[0], "prompt": v[1]} for k, v in _NARRATION.items()
        ],
        "visual_presets": [
            {"id": k, "label": v[0], "description": v[1], "prompt": v[2]} for k, v in _VISUAL.items()
        ],
        "defaults": {
            "narration_style_preset": DEFAULT_NARRATION_PRESET,
            "visual_style_preset": DEFAULT_VISUAL_PRESET,
        },
    }
