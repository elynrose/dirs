"""Prompt/runtime helper functions shared across Phase 3 and export flows."""

from __future__ import annotations

from typing import Any, Literal

from director_api.db.models import Chapter, Project, Scene
from director_api.services.image_prompt_assembly import (
    assemble_scene_still_image_prompt,
    assemble_scene_video_prompt,
    prompt_declares_no_humans,
    prompt_leading_shot_tag,
    scene_text_for_character_match,
    should_append_framing_safety_positive,
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


def _prompt_declares_no_humans(prompt: str | None) -> bool:
    return prompt_declares_no_humans(prompt)


def _prompt_leading_shot_tag(prompt: str | None) -> str | None:
    return prompt_leading_shot_tag(prompt)


def _should_append_framing_safety_positive(prompt: str, *, character_prefix_injected: bool) -> bool:
    return should_append_framing_safety_positive(
        prompt, character_prefix_injected=character_prefix_injected
    )


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
    return assemble_scene_still_image_prompt(
        db,
        scene,
        project,
        settings,
        prompt,
        append_framing_safety=True,
        append_era_anchor=True,
    )


def _resolve_phase3_video_text_prompt(
    scene: Scene,
    pp: dict[str, Any],
    *,
    override: Any = None,
    project: Project | None = None,
    settings: Any | None = None,
    suffix: Any = None,
) -> str:
    """Base video text before character/camera polish (motion hints, dialogue, package fields)."""
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


def _scene_video_prompt_for_provider(
    db: Any,
    scene: Scene,
    project: Project,
    settings: Any,
    *,
    override: Any = None,
    suffix: Any = None,
    exclude_character_bible: bool = False,
    automation_character_prefix: str | None = None,
) -> str:
    """Video prompt as fal / Comfy WAN workers send it (resolved-prompt preview parity)."""
    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    base = _resolve_phase3_video_text_prompt(
        scene,
        pp,
        override=override,
        project=project,
        settings=settings,
        suffix=suffix,
    )
    return assemble_scene_video_prompt(
        db,
        scene,
        project,
        settings,
        base,
        exclude_character_bible=exclude_character_bible,
        automation_character_prefix=automation_character_prefix,
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
