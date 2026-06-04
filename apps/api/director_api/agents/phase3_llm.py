"""Optional LLM scene-plan refinement (validated against scene-plan-batch/v1)."""

from __future__ import annotations

import json
from typing import Any

from director_api.agents.phase2_llm import _chat_json_object_ex
from director_api.config import Settings
from director_api.services.llm_prompt_runtime import get_llm_prompt_text
from director_api.services.research_service import sanitize_jsonb_text


def _chat_json_object(
    settings: Settings,
    *,
    system: str,
    user: str,
    service_type: str,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    data, _err = _chat_json_object_ex(
        settings,
        system=system,
        user=user,
        service_type=service_type,
        usage_sink=usage_sink,
        temperature=0.35,
    )
    return data


def refine_scene_plan_batch(
    seed_batch: dict[str, Any],
    *,
    chapter_title: str,
    project_topic: str,
    settings: Settings,
    narration_style: str | None = None,
    planning_hints: dict[str, Any] | None = None,
    target_duration_sec: int | None = None,
    character_bible: str | None = None,
    frame_aspect_ratio: str | None = None,
    visual_style_resolved: str | None = None,
    visual_preset_id: str | None = None,
    usage_sink: list[dict[str, Any]] | None = None,
    no_narration: bool = False,
) -> dict[str, Any] | None:
    """Return scene-plan-batch/v1 or None to keep seed."""
    sys = get_llm_prompt_text("phase3_scene_plan_refine_base")
    vp = (visual_preset_id or "").strip().lower()
    if vp == "three_d_animation":
        sys += (
            " VISUAL MEDIUM (hard rule): project uses stylized 3D CG feature animation only. "
            "Every image_prompt and video_prompt must describe STYLIZED 3D CGI (rounded forms, soft shading, "
            "subsurface scattering)—never photoreal live-action documentary, never flat 2D cel or hand-drawn line art. "
            "Preserve the full visual_style_resolved clause from user JSON at the start of each image_prompt."
        )
    elif vp == "hand_drawn_2d":
        sys += (
            " VISUAL MEDIUM (hard rule): project uses hand-drawn 2D animation only—never 3D CGI, never photoreal "
            "live-action. Preserve visual_style_resolved in each image_prompt."
        )
    elif vp in ("flat_infographic",):
        sys += (
            " VISUAL MEDIUM (hard rule): flat vector / infographic look only—not photoreal, not 3D CGI. "
            "Preserve visual_style_resolved in each image_prompt."
        )
    if no_narration:
        sys += (
            " NO_NARRATION MODE (hard rule): this project has no voice-over. Set every scene's "
            "narration_text to exactly \".\" (a single period). Put the story beat, subjects, and "
            "visual intent in purpose, image_prompt, and video_prompt instead — not in narration_text. "
            "Do not write spoken documentary VO."
        )
    if planning_hints:
        sys += (
            " The user JSON includes planning_hints: blend the seed's editorial structure (paragraph/beat "
            "boundaries, idea flow) with those production targets. "
            "estimated_narration_sec and speaking_rate_wpm_assumption define rough total spoken time; "
            "scene_clip_duration_sec is the typical generated video segment length. "
            "Use at least scene_count_min scenes (never fewer), and at most scene_count_max; aim around suggested_scene_count "
            "when it fits the script—more scenes than the minimum are encouraged when beats warrant it. "
            "If the seed has far fewer scenes than scene_count_min, split long narration_text at natural pauses into multiple scenes; "
            "if far more very short scenes, merge only adjacent beats that belong to one idea (while respecting scene_count_min). "
            "Set each planned_duration_sec from its narration_text length using ~130 wpm, clamped 5–600; "
            "treat each value as at least (estimated spoken time + ~5 seconds) of on-screen hold after the VO ends—exports enforce that floor once audio exists. "
            "Prefer durations that align sensibly with scene_clip_duration_sec (e.g. roughly one to a few clips per scene)."
        )
    if target_duration_sec is not None and int(target_duration_sec) > 0:
        t = int(target_duration_sec)
        sys += (
            f" User JSON includes chapter_target_duration_sec={t}: sum of all scenes' planned_duration_sec should "
            f"stay within about ±35% of that target (roughly {int(t * 0.65)}–{int(t * 1.35)} seconds total). "
            "Adjust per-scene durations or scene splits to hit that band—large drift hurts automated chapter pacing review."
        )
    if (narration_style or "").strip() and not no_narration:
        sys += " Voice brief for all narration_text: " + (narration_style.strip()[:1200])
    if (character_bible or "").strip():
        sys += (
            " User JSON may include character_bible: canonical visual identities for recurring figures. "
            "When a scene’s narration implies a named or recurring character, align image_prompt and video_prompt "
            "(framing and motion) with their visual bible (wardrobe, face, age) and do not contradict it."
        )
    far = (frame_aspect_ratio or "").strip()
    if far in ("16:9", "9:16"):
        sys += (
            f" User JSON includes frame_aspect_ratio={far!r}: compose every image_prompt and video_prompt for this "
            "delivery shape—widescreen landscape (16:9) vs vertical portrait (9:16). Do not assume the other format."
        )
    sys += (
        " Vary camera angle and perspective across scenes in the chapter (low/high angle, side, from behind, "
        "overhead, ground level)—not only eye-level medium shots. image_prompt and video_prompt must agree on angle."
    )
    user_obj: dict[str, Any] = {
        "seed": seed_batch,
        "chapter_title": chapter_title,
        "topic": project_topic[:4000],
    }
    vsr = (visual_style_resolved or "").strip()
    if vsr:
        user_obj["visual_style_resolved"] = vsr[:4000]
    if vp:
        user_obj["visual_preset_id"] = vp
    if far in ("16:9", "9:16"):
        user_obj["frame_aspect_ratio"] = far
    if target_duration_sec is not None and int(target_duration_sec) > 0:
        user_obj["chapter_target_duration_sec"] = int(target_duration_sec)
    if planning_hints:
        user_obj["planning_hints"] = planning_hints
    if (character_bible or "").strip():
        user_obj["character_bible"] = character_bible.strip()[:8000]
    user = json.dumps(user_obj, ensure_ascii=False)
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase3_scene_plan_refine", usage_sink=usage_sink
    )
    if not out or out.get("schema_id") != "scene-plan-batch/v1":
        return None
    for s in out.get("scenes") or []:
        if isinstance(s.get("narration_text"), str):
            s["narration_text"] = sanitize_jsonb_text(s["narration_text"], 12_000)
        st = s.get("stock_search_terms")
        if isinstance(st, list):
            cleaned_st = [
                sanitize_jsonb_text(str(x), 80).strip() for x in st if x is not None and str(x).strip()
            ][:8]
            if cleaned_st:
                s["stock_search_terms"] = cleaned_st
            else:
                s.pop("stock_search_terms", None)
        elif isinstance(st, str) and st.strip():
            s["stock_search_terms"] = [sanitize_jsonb_text(st.strip(), 80)]
        else:
            s.pop("stock_search_terms", None)
        pp = s.get("prompt_package_json")
        if isinstance(pp, dict):
            if isinstance(pp.get("image_prompt"), str):
                pp["image_prompt"] = sanitize_jsonb_text(pp["image_prompt"], 4000)
            if isinstance(pp.get("negative_prompt"), str):
                pp["negative_prompt"] = sanitize_jsonb_text(pp["negative_prompt"], 1200)
            if isinstance(pp.get("video_prompt"), str):
                pp["video_prompt"] = sanitize_jsonb_text(pp["video_prompt"], 3000)
    return out


def extend_scene_plan_batch(
    existing_scenes: list[dict[str, Any]],
    *,
    chapter_title: str,
    chapter_script: str,
    chapter_summary: str,
    project_topic: str,
    settings: Settings,
    narration_style: str | None = None,
    target_duration_sec: int | None = None,
    scene_clip_sec: int = 10,
    character_bible: str | None = None,
    frame_aspect_ratio: str | None = None,
    visual_style_resolved: str | None = None,
    visual_preset_id: str | None = None,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return scene-plan-batch/v1 with exactly one new scene appended in narrative terms, or None on failure."""
    sys = get_llm_prompt_text("phase3_scene_extend_base")
    vp_x = (visual_preset_id or "").strip().lower()
    if vp_x == "three_d_animation":
        sys += " Match stylized 3D CG animation only—never 2D cel or photoreal live-action."
    elif vp_x == "hand_drawn_2d":
        sys += " Match hand-drawn 2D animation only—never 3D CGI or photoreal."
    if target_duration_sec is not None and int(target_duration_sec) > 0:
        t = int(target_duration_sec)
        sys += (
            f" Chapter target duration is about {t}s total across all scenes; the new scene should not blow pacing—"
            "keep this addition proportionate (typically one clip to a few clips worth of VO)."
        )
    if (narration_style or "").strip():
        sys += " Voice brief for narration_text: " + (narration_style.strip()[:1200])
    if (character_bible or "").strip():
        sys += (
            " User JSON may include character_bible: align image_prompt and video_prompt with recurring figures when narration implies them."
        )
    far = (frame_aspect_ratio or "").strip()
    if far in ("16:9", "9:16"):
        sys += (
            f" User JSON includes frame_aspect_ratio={far!r}: compose image_prompt and video_prompt for this delivery "
            "shape (16:9 landscape vs 9:16 portrait)."
        )
    sys += (
        " Pick a camera angle that differs from the previous scene when possible (not only eye-level medium shots)."
    )
    user_obj: dict[str, Any] = {
        "existing_scenes": existing_scenes,
        "chapter_title": chapter_title,
        "chapter_script": (chapter_script or "")[:12000],
        "chapter_summary": (chapter_summary or "")[:4000],
        "topic": (project_topic or "")[:4000],
        "scene_clip_duration_sec": int(scene_clip_sec),
    }
    vsr_x = (visual_style_resolved or "").strip()
    if vsr_x:
        user_obj["visual_style_resolved"] = vsr_x[:4000]
    if vp_x:
        user_obj["visual_preset_id"] = vp_x
    if far in ("16:9", "9:16"):
        user_obj["frame_aspect_ratio"] = far
    if target_duration_sec is not None and int(target_duration_sec) > 0:
        user_obj["chapter_target_duration_sec"] = int(target_duration_sec)
    if (character_bible or "").strip():
        user_obj["character_bible"] = character_bible.strip()[:8000]
    user = json.dumps(user_obj, ensure_ascii=False)
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase3_scene_extend", usage_sink=usage_sink
    )
    if not out or out.get("schema_id") != "scene-plan-batch/v1":
        return None
    raw_scenes = out.get("scenes")
    if not isinstance(raw_scenes, list) or len(raw_scenes) < 1:
        return None
    # Keep the first scene only if the model returned extras.
    out["scenes"] = raw_scenes[:1]
    for s in out.get("scenes") or []:
        if isinstance(s.get("narration_text"), str):
            s["narration_text"] = sanitize_jsonb_text(s["narration_text"], 12_000)
        st = s.get("stock_search_terms")
        if isinstance(st, list):
            cleaned_st = [
                sanitize_jsonb_text(str(x), 80).strip() for x in st if x is not None and str(x).strip()
            ][:8]
            if cleaned_st:
                s["stock_search_terms"] = cleaned_st
            else:
                s.pop("stock_search_terms", None)
        elif isinstance(st, str) and st.strip():
            s["stock_search_terms"] = [sanitize_jsonb_text(st.strip(), 80)]
        else:
            s.pop("stock_search_terms", None)
        pp = s.get("prompt_package_json")
        if isinstance(pp, dict):
            if isinstance(pp.get("image_prompt"), str):
                pp["image_prompt"] = sanitize_jsonb_text(pp["image_prompt"], 4000)
            if isinstance(pp.get("negative_prompt"), str):
                pp["negative_prompt"] = sanitize_jsonb_text(pp["negative_prompt"], 1200)
            if isinstance(pp.get("video_prompt"), str):
                pp["video_prompt"] = sanitize_jsonb_text(pp["video_prompt"], 3000)
    return out
