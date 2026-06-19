"""Prompt/runtime helper functions shared across Phase 3 and export flows."""

from __future__ import annotations

from typing import Any, Literal

from director_api.db.models import Chapter, Project, Scene
from director_api.services import phase3 as phase3_svc
from director_api.services.camera_perspective import inject_camera_perspective_into_prompt
from director_api.services.character_prompt import prompt_already_has_character_prefix
from director_api.services.image_prompt_assembly import (
    character_consistency_block_for_image,
    polish_scene_image_prompt,
    scene_text_for_character_match,
    strip_redundant_visual_style_clauses,
)
from director_api.services.narration_bracket_visual import (
    append_video_character_dialogue_to_prompt,
    base_image_prompt_from_scene_fields,
    video_text_prompt_from_scene_fields,
)
from director_api.services.research_service import sanitize_jsonb_text
from director_api.style_presets import effective_visual_style

StillMotion = Literal["none", "pan", "zoom"]


def _normalize_still_motion(val: Any) -> StillMotion | None:
    if isinstance(val, str) and val in ("none", "pan", "zoom"):
        return val  # type: ignore[return-value]
    return None


def _timeline_still_motion_mode(tj: dict[str, Any] | None) -> StillMotion:
    if not isinstance(tj, dict):
        return "none"
    m = _normalize_still_motion(tj.get("still_motion_mode"))
    return m or "none"


def _timeline_still_motion_source(tj: dict[str, Any] | None) -> str:
    if not isinstance(tj, dict):
        return "timeline_default"
    s = str(tj.get("still_motion_source") or "timeline_default").strip()
    return s if s in ("timeline_default", "scene_video_prompt", "clip_override") else "timeline_default"


def _resolve_still_motion(
    *,
    timeline_json: dict[str, Any] | None,
    clip: dict[str, Any] | None,
    scene_video_prompt: str | None,
) -> StillMotion:
    if isinstance(clip, dict):
        clip_motion = _normalize_still_motion(clip.get("still_motion"))
        if clip_motion and clip_motion != "none":
            return clip_motion
    source = _timeline_still_motion_source(timeline_json)
    if source == "scene_video_prompt":
        _slow, _dir, slideshow_motion = _local_ffmpeg_motion_from_video_prompt(scene_video_prompt or "")
        if slideshow_motion in ("pan", "zoom"):
            return slideshow_motion  # type: ignore[return-value]
        return "none"
    return _timeline_still_motion_mode(timeline_json)


def _manifest_requires_still_motion_encode(manifest: list[dict[str, Any]]) -> bool:
    for m in manifest:
        if str(m.get("asset_type") or "").lower() != "image":
            continue
        if str(m.get("still_motion") or "none") in ("pan", "zoom"):
            return True
    return False


_FRAMING_SAFETY_NEG = (
    "cropped head, cropped face, head out of frame, face cut off, decapitated subject, "
    "headless figure, head touching upper frame edge, top of head clipped, hairline clipped, "
    "partial face, partial subject, subject too close to edge, awkward crop, off-center crop"
)

_FRAMING_SAFETY_POS = (
    "Composition safety: keep the subject's full head and shoulders inside the frame with "
    "breathing room above the crown; the top of the head must sit well below the upper edge "
    "of the image; do not crop the face."
)

_FRAMING_TAGS_NO_HUMAN_HEAD = {"[ECU]", "[INSERT]", "[BROLL]"}

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


def _prompt_leading_shot_tag(prompt: str | None) -> str | None:
    """Return the bracketed SHOT_TAG at the very start of ``prompt`` (e.g. ``[CU]``), or ``None``."""
    if not prompt:
        return None
    s = prompt.lstrip()
    if not s.startswith("["):
        return None
    end = s.find("]")
    if end <= 1 or end > 12:
        return None
    return s[: end + 1].upper()


def _prompt_declares_no_humans(prompt: str | None) -> bool:
    """True when the image prompt is explicitly people-free."""
    if not prompt:
        return False
    if _prompt_leading_shot_tag(prompt) in _FRAMING_TAGS_NO_HUMAN_HEAD:
        return True
    lowered = prompt.lower()
    return any(phrase in lowered for phrase in _NO_PEOPLE_PHRASES)


def _should_append_framing_safety_positive(prompt: str, *, character_prefix_injected: bool) -> bool:
    """Only nudge framing on shots that actually contain a human subject."""
    if _prompt_declares_no_humans(prompt):
        return False
    return bool(character_prefix_injected)


def _merge_framing_safety_negative(scene_neg: str | None) -> str | None:
    """Always tack the anti-crop tokens onto whatever scene-level negative_prompt is set."""
    base = (scene_neg or "").strip()
    if not base:
        return sanitize_jsonb_text(_FRAMING_SAFETY_NEG, 1200)
    probe = "cropped head"
    if probe in base.lower():
        return sanitize_jsonb_text(base, 1200)
    return sanitize_jsonb_text(f"{base}, {_FRAMING_SAFETY_NEG}", 1200)


def _scene_era_anchor(
    scene: Scene,
    chapter: Chapter | None,
    project: Project | None,
    *,
    max_chars: int = 160,
) -> str:
    """Return a short ``Set in: <era/place>.`` clause for video prompts."""
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


def _scene_text_for_character_match(db: Any, scene: Scene) -> str:
    return scene_text_for_character_match(db, scene)


def _scene_still_prompt_for_comfy(db: Any, scene: Scene, project: Project, settings: Any) -> str:
    """Same prompt recipe as scene image generation (Flux / Comfy still), without job payload overrides."""
    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    vis_style = effective_visual_style(project.visual_style, settings)
    prompt, _, _ = base_image_prompt_from_scene_fields(
        narration_text=scene.narration_text,
        prompt_package_json=pp,
        image_prompt_override=None,
        visual_style_effective=vis_style,
    )
    prompt = str(prompt).strip()
    if vis_style:
        prompt = strip_redundant_visual_style_clauses(prompt, vis_style)
    character_prefix_injected = False
    if not _prompt_declares_no_humans(prompt):
        scene_match_text = _scene_text_for_character_match(db, scene)
        prefix = character_consistency_block_for_image(
            db,
            project.id,
            scene_text=scene_match_text,
            base_prompt=prompt,
            max_chars=2000,
        )
        if prefix and not prompt_already_has_character_prefix(prompt, prefix):
            room = max(400, 4000 - len(prefix) - 3)
            prompt = f"{str(prompt)[:room]}\n\n{prefix}"
            character_prefix_injected = True
        elif prefix:
            character_prefix_injected = True

    prompt = inject_camera_perspective_into_prompt(
        prompt,
        scene_key=str(scene.id),
        order_index=int(scene.order_index),
        for_video=False,
        max_total=4000,
    )

    if _should_append_framing_safety_positive(
        prompt, character_prefix_injected=character_prefix_injected
    ):
        room_fr = max(0, 4000 - len(prompt) - 2)
        if room_fr > len(_FRAMING_SAFETY_POS):
            prompt = f"{prompt}\n\n{_FRAMING_SAFETY_POS}"

    try:
        chapter = db.get(Chapter, scene.chapter_id) if scene.chapter_id else None
    except Exception:  # noqa: BLE001
        chapter = None
    era_anchor = _scene_era_anchor(scene, chapter, project)
    if era_anchor:
        room_ea = max(0, 4000 - len(prompt) - 2)
        if room_ea > len(era_anchor):
            prompt = f"{prompt}\n\n{era_anchor}"
    pid_vis = phase3_svc.resolve_visual_preset_id_for_project(project, settings)
    prompt = polish_scene_image_prompt(
        prompt,
        vis_style=vis_style,
        visual_preset_id=pid_vis,
        max_total=4000,
        mood=(scene.purpose or "")[:240] or None,
    )
    return str(prompt)


def _resolve_phase3_video_text_prompt(
    scene: Scene,
    pp: dict[str, Any],
    *,
    override: Any = None,
    project: Project | None = None,
    settings: Any | None = None,
    suffix: Any = None,
) -> str:
    """Text sent to generative video models; optional job override, else package, else VO/purpose hints."""
    vis_eff: str | None = None
    if project is not None and settings is not None:
        vis_eff = effective_visual_style(project.visual_style, settings)
    base = video_text_prompt_from_scene_fields(
        narration_text=scene.narration_text,
        purpose=scene.purpose,
        visual_type=scene.visual_type,
        prompt_package_json=pp if isinstance(pp, dict) else {},
        video_prompt_override=override if isinstance(override, str) else None,
        visual_style_effective=vis_eff,
        video_prompt_suffix=suffix if isinstance(suffix, str) else None,
    )
    if project is None:
        return base
    dial = pp.get("video_character_dialogue") if isinstance(pp.get("video_character_dialogue"), str) else None
    return append_video_character_dialogue_to_prompt(
        base,
        include_spoken_dialogue_in_video_prompt=bool(
            getattr(project, "include_spoken_dialogue_in_video_prompt", False)
        ),
        video_character_dialogue=dial,
    )


def _local_ffmpeg_motion_from_video_prompt(prompt: str) -> tuple[bool, str, str]:
    """Coarse motion hints from natural-language ``video_prompt`` for still→MP4 / slideshow."""
    t = (prompt or "").lower()
    has_pan = any(
        p in t
        for p in (
            "pan left",
            "pan right",
            "panning",
            "camera pans",
            "lateral move",
            "truck left",
            "truck right",
            "whip pan",
        )
    )
    zoom_out = any(p in t for p in ("zoom out", "pull out", "pull back", "dolly out", "pull-back", "widen"))
    zoom_in = any(
        p in t
        for p in (
            "zoom in",
            "push in",
            "push-in",
            "dolly in",
            "slow zoom",
            "creep in",
            "tighter",
            "closing in",
            "push closer",
        )
    )
    if has_pan and not zoom_in and not zoom_out and "zoom" not in t:
        return False, "in", "pan"
    if zoom_out:
        return True, "out", "zoom"
    if zoom_in or ("zoom" in t and not zoom_out):
        return True, "in", "zoom"
    return False, "in", "none"
