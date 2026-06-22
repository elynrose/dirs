"""Structured Flux / diffusion prompts by visual preset (image + video).

Flux and similar models respond well to labeled sections (subject, treatment, environment,
composition, lighting, rendering, mood) rather than one long paragraph.
"""

from __future__ import annotations

import re
from typing import Any

from director_api.services.camera_perspective import prompt_already_specifies_camera_angle
from director_api.services.research_service import sanitize_jsonb_text
from director_api.style_presets import DEFAULT_VISUAL_PRESET, visual_preset_ids

_CAMERA_IMAGE_RE = re.compile(
    r"^Camera perspective:\s*.+?(?:\.\s*)?$",
    re.IGNORECASE | re.MULTILINE,
)
_CAMERA_VIDEO_RE = re.compile(
    r"^Camera motion:\s*.+?(?:\.\s*)?$",
    re.IGNORECASE | re.MULTILINE,
)
_SETTING_PIPE_RE = re.compile(r"\s*\|\s*Setting:\s*.+$", re.IGNORECASE | re.DOTALL)
_SET_IN_RE = re.compile(r"^Set in:\s*.+?(?:\.\s*)?$", re.IGNORECASE | re.MULTILINE)
_CHARACTER_LEAD_RE = re.compile(
    r"^CHARACTER CONSISTENCY\s*—\s*.+$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_VISUAL_STYLE_TAIL_RE = re.compile(r"\n+\s*(Visual style|Style):\s*.+$", re.IGNORECASE | re.DOTALL)
_AVOID_TAIL_RE = re.compile(r"\n+\s*Avoid in image:\s*.+$", re.IGNORECASE | re.DOTALL)
_BOILER_RE = re.compile(
    r"Single (?:photoreal|full-frame)[^.]+\.\s*",
    re.IGNORECASE,
)

# Per-preset Flux section bodies (comma-phrases work well in FLUX Dev).
_PRESET_FLUX_BLOCKS: dict[str, dict[str, str]] = {
    "three_d_animation": {
        "style_design": (
            "Pixar-inspired 3D CGI character design, expressive eyes, stylized proportions, smooth shaded surfaces, "
            "appealing character design, subtle asymmetry, cinematic animation quality, subsurface scattering. "
            "Strictly NOT 2D hand-drawn cel, flat cartoon, or inked line art"
        ),
        "lighting": (
            "Warm cinematic lighting, soft global illumination, volumetric light rays, cinematic rim lighting, "
            "motivated key light"
        ),
        "rendering": (
            "High-end animated feature film quality, physically based rendering, detailed textures, global illumination, "
            "depth of field, 3D CGI sculptural depth"
        ),
        "mood_default": "Whimsical, emotional storytelling, heartwarming or dramatic as the beat requires",
        "motion_default": (
            "Gentle cinematic camera move, slow push or hold, maintain 3D character models and wardrobe continuity"
        ),
    },
    "hand_drawn_2d": {
        "style_design": (
            "Hand-drawn 2D animation character design, clean expressive line art, soft cel or watercolor shading, "
            "storybook warmth, visible inked contours. Strictly NOT 3D CGI or photoreal skin"
        ),
        "lighting": "Soft diffuse storybook lighting, gentle color washes, warm highlight accents",
        "rendering": (
            "Traditional 2D animation frame quality, painterly background depth, flat or gentle cel shading, "
            "illustrated composition"
        ),
        "mood_default": "Warm, storybook, emotionally readable character acting",
        "motion_default": "Subtle 2D animation hold or gentle pan, same drawn characters and palette as the still",
    },
    "flat_infographic": {
        "style_design": (
            "Flat vector infographic design, simplified geometric shapes, solid color fills, clear visual hierarchy, "
            "modern editorial diagram look. Strictly NOT photoreal scenes or 3D renders"
        ),
        "lighting": "Even flat illumination, minimal shadows, high clarity for information graphics",
        "rendering": "Crisp vector edges, solid flat colors, clean layout, infographic poster quality",
        "mood_default": "Clear, educational, confident editorial tone",
        "motion_default": "Minimal motion graphics drift, preserve flat vector layout and color blocks",
    },
    "cinematic_documentary": {
        "style_design": (
            "Photoreal documentary cinematography, natural skin and fabric texture, observational realism, "
            "believable cast on real locations. Strictly NOT illustration, cartoon, or vector art"
        ),
        "lighting": "Natural motivated light, shallow depth of field, subtle film grain, authentic color",
        "rendering": (
            "35mm or digital cinema quality, sharp hero subject, readable real-world environment, "
            "accurate materials"
        ),
        "mood_default": "Observational, grounded, authentic documentary storytelling",
        "motion_default": "Observational documentary camera move, slow push or gentle pan, stable naturalistic feel",
    },
    "archival_historical": {
        "style_design": (
            "Authentic archival historical photograph, period-accurate subjects and props, believable aging, "
            "monochrome or sepia tone. Strictly NOT modern illustration or faux-vintage painting"
        ),
        "lighting": "Period-appropriate soft contrast, visible film grain, gentle vignette, faded highlights",
        "rendering": "Scanned negative or period-camera quality, scratches and edge wear optional, historical authenticity",
        "mood_default": "Historical gravitas, authentic record-of-the-past feeling",
        "motion_default": "Minimal archival drift, very slow push, preserve period setting and grain",
    },
    "aerial_epic": {
        "style_design": (
            "Photoreal aerial cinematography, sweeping terrain and sky, atmospheric haze, realistic scale. "
            "Strictly NOT illustrated map or fantasy matte painting"
        ),
        "lighting": "Natural daylight, atmospheric perspective, golden or blue hour optional, wide exposure latitude",
        "rendering": "Drone or helicopter plate quality, high terrain detail, cinematic wide composition",
        "mood_default": "Epic scale, awe, vast landscape grandeur",
        "motion_default": "Slow aerial drift or crane move, reveal landscape scale, maintain geographic continuity",
    },
    "noir_dramatic": {
        "style_design": (
            "Photoreal film-noir reenactment, live actors, period wardrobe, hard shadows, tense body language. "
            "Strictly NOT comic-book inking or cel animation"
        ),
        "lighting": "High-contrast low-key lighting, deep shadows, silhouettes, practical haze, motivated single sources",
        "rendering": "Black-and-white or desaturated cinematic grade, sharp subject separation, noir atmosphere",
        "mood_default": "Tense, dramatic, suspenseful noir storytelling",
        "motion_default": "Slow noir dolly or push, maintain hard shadows and period wardrobe continuity",
    },
    "sci_tech_cgi": {
        "style_design": (
            "Photoreal sci-fi CGI hardware and environments, brushed metal, glass, emissive panels, plausible scale. "
            "Strictly NOT cartoon sci-fi or flat icon art"
        ),
        "lighting": "Rim light, motivated practicals, subtle holographic UI as diegetic elements only",
        "rendering": "Modern VFX film quality, ultra-sharp materials, believable futuristic set dressing",
        "mood_default": "Sleek, futuristic, technologically credible",
        "motion_default": "Slow sci-fi camera push, subtle parallax on hardware, maintain material continuity",
    },
    "cinematic_historical_epic": {
        "style_design": (
            "Photoreal cinematic historical epic, live cast in period armor and costume, massive practical sets, "
            "sweeping composition. Strictly NOT oil-painting illustration or game cinematic abandon of real skin"
        ),
        "lighting": "Golden-hour or torchlight motivated drama, rich color grade, epic scale lighting",
        "rendering": "Prestige period-film quality, accurate fabrics and materials, sweeping anamorphic feel",
        "mood_default": "Epic, dramatic, high-stakes historical storytelling",
        "motion_default": "Slow epic dolly or crane, maintain cast, armor, and set continuity",
    },
}

_DEFAULT_MOOD = "Cinematic emotional storytelling appropriate to the beat"
_DEFAULT_MOTION = "Gentle cinematic camera move, maintain subject, wardrobe, and setting continuity"

_FLUX_SECTION_LABELS = (
    "Subject",
    "Visual treatment",
    "Environment",
    "Composition",
    "Lighting",
    "Rendering",
    "Mood",
    "Motion",
)


def is_labeled_flux_prompt(text: str) -> bool:
    """True when the prompt is already in labeled Flux section form (do not re-parse)."""
    t = (text or "").strip()
    if not t:
        return False
    if not re.search(r"(?m)^Subject:\s*\S", t):
        return False
    other_labels = sum(
        1
        for label in _FLUX_SECTION_LABELS[1:]
        if re.search(rf"(?m)^{re.escape(label)}:\s*\S", t)
    )
    return other_labels >= 1


def normalize_labeled_flux_prompt(text: str, *, max_total: int = 4000) -> str:
    """Light cleanup for prompts that are already structured — preserve scene content."""
    t = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    return sanitize_jsonb_text(t, max_total)


def inject_characters_into_labeled_prompt(
    text: str,
    character_block: str,
    *,
    max_total: int = 4000,
) -> str:
    """Append character bible into the Subject section without breaking labeled structure."""
    raw = (character_block or "").strip()
    if not raw:
        return normalize_labeled_flux_prompt(text, max_total=max_total)
    cb = re.sub(r"^CHARACTER CONSISTENCY\s*—\s*", "", raw, flags=re.I).strip()
    if not cb:
        return normalize_labeled_flux_prompt(text, max_total=max_total)
    t = (text or "").strip()
    if not is_labeled_flux_prompt(t):
        combined = f"{t}\n\n{raw}" if t else raw
        return sanitize_jsonb_text(combined, max_total)

    def _repl(m: re.Match[str]) -> str:
        body = m.group(2).rstrip()
        merged = f"{body}. {cb}" if body else cb
        return f"{m.group(1)}{merged}"

    out = re.sub(
        r"(?ms)^(Subject:\s*)(.+?)(?=\n\n(?:Visual treatment|Environment|Composition|Lighting|Rendering|Mood|Motion):|\Z)",
        _repl,
        t,
        count=1,
    )
    return normalize_labeled_flux_prompt(out, max_total=max_total)


def _preset_blocks(preset_id: str | None) -> dict[str, str]:
    pid = (preset_id or "").strip().lower()
    if pid in _PRESET_FLUX_BLOCKS:
        return _PRESET_FLUX_BLOCKS[pid]
    if pid in visual_preset_ids():
        return _PRESET_FLUX_BLOCKS.get(DEFAULT_VISUAL_PRESET, _PRESET_FLUX_BLOCKS["cinematic_documentary"])
    return _PRESET_FLUX_BLOCKS[DEFAULT_VISUAL_PRESET]


def _first_nonempty_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _extract_framing_from_subject(subject: str) -> str:
    """Pull an explicit shot/framing clause out of the subject when present (e.g. Dutch angle, wide establishing)."""
    s = (subject or "").strip()
    if not s or not prompt_already_specifies_camera_angle(s):
        return ""
    # Prefer the opening sentence — scene planners usually lead with framing.
    first = s.split(".")[0].strip()
    if first and prompt_already_specifies_camera_angle(first):
        return first[:240]
    return ""


def _extract_camera_line(body: str, *, for_video: bool) -> tuple[str, str]:
    p = body or ""
    pat = _CAMERA_VIDEO_RE if for_video else _CAMERA_IMAGE_RE
    m = pat.search(p)
    if not m:
        alt = _CAMERA_IMAGE_RE if for_video else _CAMERA_VIDEO_RE
        m = alt.search(p)
    if m:
        cam = m.group(0).strip()
        rest = (p[: m.start()] + p[m.end() :]).strip()
        # Normalize label for composition section
        comp = re.sub(r"^Camera (perspective|motion):\s*", "", cam, flags=re.I).strip().rstrip(".")
        return rest, comp
    return p, ""


def _split_loose_prompt(loose: str, *, for_video: bool) -> dict[str, str]:
    """Pull subject, environment, composition, and character tags out of a legacy assembled prompt."""
    p = (loose or "").strip()
    environment = ""
    characters = ""

    m_set = _SETTING_PIPE_RE.search(p)
    if m_set:
        environment = m_set.group(0).replace("|", "").replace("Setting:", "").strip()
        p = p[: m_set.start()].strip()

    m_in = _SET_IN_RE.search(p)
    if m_in:
        environment = m_in.group(0).replace("Set in:", "").strip().rstrip(".")
        p = (p[: m_in.start()] + p[m_in.end() :]).strip()

    m_char = _CHARACTER_LEAD_RE.search(p)
    if m_char:
        characters = m_char.group(0).strip()
        p = (p[: m_char.start()] + p[m_char.end() :]).strip()
    elif " || " in p and re.search(r"^[A-Za-z][^:\n]{0,40}:", p, re.M):
        # Short tags: "Samson: … || Delilah: …"
        lines = [ln.strip() for ln in p.splitlines() if " || " in ln]
        if lines:
            characters = lines[-1][:800]
            p = p.replace(characters, "").strip()

    p = _VISUAL_STYLE_TAIL_RE.sub("", p)
    p = _AVOID_TAIL_RE.sub("", p)
    p = _BOILER_RE.sub("", p)
    p, composition = _extract_camera_line(p, for_video=for_video)

    # Drop leading style-only marker paragraphs (never drop paragraphs with concrete scene nouns).
    parts = re.split(r"\n\s*\n", p.strip())
    while len(parts) > 1:
        head = parts[0].lower()
        head_is_style_only = any(
            k in head
            for k in (
                "stylized 3d",
                "hand-drawn 2d",
                "flat vector",
                "authentic archival",
                "film noir",
                "strictly forbidden",
            )
        ) or (
            head.startswith("visual treatment:")
            and len(head) < 220
        )
        if head_is_style_only:
            parts = parts[1:]
        else:
            break
    subject = "\n\n".join(parts).strip()
    subject = re.sub(r"\s+", " ", subject)
    return {
        "subject": subject,
        "characters": characters.strip(),
        "environment": environment.strip(),
        "composition": composition.strip(),
    }


def _section(label: str, body: str) -> str:
    b = re.sub(r"\s+", " ", (body or "").strip())
    if not b:
        return ""
    return f"{label}: {b}"


def build_flux_structured_prompt(
    *,
    subject: str,
    visual_preset_id: str | None,
    visual_style_resolved: str | None = None,
    characters: str = "",
    environment: str = "",
    composition: str = "",
    mood: str = "",
    motion: str = "",
    for_video: bool = False,
    max_total: int = 4000,
) -> str:
    """Assemble labeled Flux sections for the given visual preset."""
    blocks = _preset_blocks(visual_preset_id)
    pid = (visual_preset_id or "").strip().lower()

    subj_parts = [subject.strip()] if subject.strip() else []
    char = (characters or "").strip()
    if char and char not in subject:
        if char.upper().startswith("CHARACTER CONSISTENCY"):
            char = re.sub(r"^CHARACTER CONSISTENCY\s*—\s*", "", char, flags=re.I).strip()
        subj_parts.append(char)
    scene_text = (subj_parts[0] if subj_parts else "")[:1600]
    if len(subj_parts) > 1:
        room = max(0, 1800 - len(scene_text) - 2)
        char_trim = (subj_parts[1] or "")[:room] if room > 80 else ""
        subject_line = f"{scene_text}. {char_trim}".strip(". ")[:1800] if char_trim else scene_text[:1800]
    else:
        subject_line = scene_text[:1800]

    style = blocks["style_design"]
    vs = (visual_style_resolved or "").strip()
    if vs and pid not in _PRESET_FLUX_BLOCKS:
        # Custom / free-text visual style on the project
        style = vs[:900]

    env = environment.strip() or "As described in the subject and story context"
    comp = composition.strip()
    if not comp and subject_line:
        comp = _extract_framing_from_subject(subject_line)
    if not comp:
        comp = (
            "Eye-level medium shot, balanced cinematic framing"
            if not for_video
            else "Slow push-in, stable cinematic framing"
        )
    light = blocks["lighting"]
    render = blocks["rendering"]
    mood_line = (mood or "").strip() or blocks.get("mood_default") or _DEFAULT_MOOD
    motion_line = (motion or "").strip() or blocks.get("motion_default") or _DEFAULT_MOTION

    sections = [
        _section("Subject", subject_line),
        _section("Visual treatment", style),
        _section("Environment", env),
        _section("Composition", comp),
        _section("Lighting", light),
        _section("Rendering", render),
        _section("Mood", mood_line),
    ]
    if for_video:
        sections.append(_section("Motion", motion_line))

    out = "\n\n".join(s for s in sections if s)
    return sanitize_jsonb_text(out, max_total)


def structure_flux_scene_prompt(
    loose: str,
    *,
    visual_preset_id: str | None,
    visual_style_resolved: str | None = None,
    mood: str | None = None,
    for_video: bool = False,
    max_total: int = 4000,
) -> str:
    """Parse a legacy scene prompt and rewrite it in Flux-friendly structured sections."""
    if is_labeled_flux_prompt(loose):
        return normalize_labeled_flux_prompt(loose, max_total=max_total)

    parts = _split_loose_prompt(loose, for_video=for_video)
    comp = parts["composition"]
    if for_video and comp and not comp.lower().startswith(("slow", "gentle", "camera", "push", "pan", "dolly", "crane", "motion")):
        comp = f"Slow cinematic move; {comp}"
    return build_flux_structured_prompt(
        subject=parts["subject"],
        visual_preset_id=visual_preset_id,
        visual_style_resolved=visual_style_resolved,
        characters=parts["characters"],
        environment=parts["environment"],
        composition=comp,
        mood=(mood or "").strip(),
        for_video=for_video,
        max_total=max_total,
    )
