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
        "Narrative documentary voice: tell a clear story, not a research paper. Use plain language a curious "
        "15-year-old can follow: short-to-medium sentences, everyday words, and one main idea per sentence when "
        "possible. If you must use a technical or rare term, explain it in simple words in the same beat. Avoid "
        "stacked subordinate clauses, ornate phrasing, and dense ‘essay’ tone. Each scene’s voice-over must be "
        "at least two full sentences (real sentences—not one long chain of commas). Open with a hook and clear "
        "stakes; build turning points and cause-and-effect the listener can track. Weave facts, names, and dates "
        "only where they move the story—skip abstract survey pacing and thesis-style framing. Use concrete sensory "
        "detail where it helps. Prefer confident, simple declarative lines; rhetorical questions only sparingly. "
        "Third person for the spine; address the viewer directly only when the script calls for it. Stay factual "
        "and broadcast-safe; no clickbait or invented quotations.",
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
        "Photoreal live-action documentary: real people, real locations, and real objects as if shot on location with a "
        "cinema camera. Natural light, true-to-life color, shallow depth of field, subtle grain. Not illustration, not "
        "cartoon, not vector art, not painterly concept art.",
        "PHOTOREAL LIVE-ACTION DOCUMENTARY STILL — must look like a frame grab from 35mm or digital cinema footage of "
        "real cast on real sets or real locations. Natural light, accurate skin and fabric texture, shallow depth of field, "
        "subtle film grain, believable environment. STRICTLY FORBIDDEN: illustration, cartoon, anime, clipart, vector "
        "graphics, comic-book linework, watercolor or oil-painting look, cel shading, storybook art, or stylized concept "
        "painting. No invented graphic poster look.",
    ),
    "archival_historical": (
        "Archival / Historical Stills",
        "Authentic historical photograph: period detail, monochrome or sepia, real film grain, soft contrast, aging "
        "artifacts (scratches, vignette). Must read as a genuine photo from the era, not a modern illustration mimicking "
        "old paper.",
        "AUTHENTIC ARCHIVAL PHOTOGRAPH — as if from a period camera or scanned negative: period-accurate subjects and "
        "props, monochrome or sepia, visible grain, soft contrast, light fading, believable aging (scratches, edge "
        "vignette). STRICTLY FORBIDDEN: digital illustration, cartoon, clean vector, painterly faux-vintage, or stylized "
        "caricature of history.",
    ),
    "aerial_epic": (
        "Aerial / Epic Landscape",
        "Photoreal aerial cinematography over real terrain: drone or helicopter plate, atmospheric haze, natural color, "
        "high detail. Not a matte painting, not an illustrated map, not a fantasy painting.",
        "PHOTOREAL AERIAL / DRONE FOOTAGE STILL — sweeping real landscape, natural sky and haze, realistic scale and "
        "terrain detail, cinematic wide composition, natural color grading. STRICTLY FORBIDDEN: illustrated landscape, "
        "painterly concept art, low-poly 3D, fantasy map graphics, or stylized matte-painting illustration.",
    ),
    "noir_dramatic": (
        "Noir / Dramatic Reenactment",
        "Photoreal film-noir reenactment: live actors, high-contrast black-and-white, hard shadows, practical haze, "
        "period wardrobe on real sets. Not comic-book noir, not graphic-novel shading.",
        "PHOTOREAL FILM NOIR REENACTMENT — live-action B&W cinematography, hard low-key lighting, deep shadows, "
        "silhouettes, subtle atmospheric haze, tense framing, period-accurate costumes on real people in real space. "
        "STRICTLY FORBIDDEN: comic illustration, inked graphic novel look, cel animation, or cartoon silhouettes.",
    ),
    "three_d_animation": (
        "Stylized 3D Animation",
        "INTENTIONALLY NON-PHOTOGRAPHIC: stylized 3D feature animation — rounded forms, expressive eyes, smooth shaded "
        "surfaces, saturated color, global illumination. Clearly CG characters, not real actors.",
        "STYLIZED 3D ANIMATED FILM STILL — clearly computer-generated characters and worlds (Pixar/DreamWorks-style "
        "family animation): soft rounded forms, large expressive eyes, smooth surfaces, saturated color, cinematic GI and "
        "subsurface scattering. Do NOT blend toward photoreal humans or documentary footage; avoid uncanny live-action mix.",
    ),
    "hand_drawn_2d": (
        "Hand-Drawn 2D",
        "INTENTIONALLY NON-PHOTOGRAPHIC: 2D drawn animation — visible line art, flat or soft shading, watercolor or "
        "gouache texture, storybook warmth. Not a photo, not 3D render.",
        "HAND-DRAWN 2D ANIMATION STILL — clean line art, expressive drawn characters, soft cel or watercolor shading, "
        "warm storybook palette. STRICTLY FORBIDDEN: photoreal skin, photographic backgrounds, or 3D-rendered realism.",
    ),
    "flat_infographic": (
        "Flat / Infographic",
        "INTENTIONALLY NON-PHOTOGRAPHIC: flat vector / infographic — simple geometry, solid fills, clear hierarchy, "
        "modern educational layout. Not a photograph of a poster; graphic design look.",
        "FLAT VECTOR INFOGRAPHIC STILL — simplified geometric shapes, solid flat colors, clear typographic hierarchy if "
        "needed, crisp edges, modern editorial diagram look. STRICTLY FORBIDDEN: photoreal scenes, textured photo "
        "backgrounds, 3D product renders, or painterly illustration masquerading as data viz.",
    ),
    "sci_tech_cgi": (
        "Sci-Tech CGI",
        "Photorealistic CGI of plausible sci-fi hardware: metal, glass, practical scale, dramatic light. Looks like a "
        "VFX shot from a modern sci-fi film — not cartoon sci-fi, not flat icon art.",
        "PHOTOREAL SCI-FI CGI STILL — sleek futuristic sets and props with believable materials (brushed metal, glass, "
        "emissive panels), rim light, holographic UI only as subtle diegetic elements, ultra-sharp detail. STRICTLY "
        "FORBIDDEN: cartoon rocket ships, flat comic sci-fi, low-poly game art, or children's illustrated space art.",
    ),
    "cinematic_historical_epic": (
        "Cinematic Historical Epic",
        "Photoreal historical epic: live-action cast, massive practical sets, golden-hour or torchlight, rich grade, "
        "sweeping camera. Like a frame from a prestige period film — not painted epic art.",
        "PHOTOREAL CINEMATIC HISTORICAL EPIC STILL — live-action cast in period-accurate armor and costume on large "
        "practical sets or real locations, dramatic golden-hour or motivated practical lighting, rich color grade, "
        "sweeping composition. STRICTLY FORBIDDEN: oil-painting epic, fantasy illustration, matte-painting storybook, "
        "or stylized game cinematic that abandons photographic skin and fabric.",
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
