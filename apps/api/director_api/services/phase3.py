"""Phase 3 — scene planning from chapter script (deterministic + schema-shaped for LLM merge)."""

from __future__ import annotations

import re
from typing import Any

from director_api.db.models import Chapter, Project
from director_api.services.research_service import sanitize_jsonb_text

# Match HTTP + worker gates to ``build_scene_plan_batch`` (script preferred, else summary).
MIN_CHARS_FOR_SCENE_PLANNING = 12


def is_producer_only_chapter_summary_for_vo(text: str) -> bool:
    """
    True when the string is only outline / producer metadata — not spoken narration.

    These come from ``chapter_outline_from_director`` (and similar) and must never be TTS'd or
    split into scene ``narration_text`` when the real ``script_text`` is still missing.
    """
    t = (text or "").strip()
    if len(t) < 8:
        return False
    chunks = [c.strip() for c in re.split(r"\n\s*\n+", t) if c.strip()]
    if not chunks:
        chunks = [t]
    for c in chunks:
        cl = c.lower()
        if cl.startswith("outline beat for"):
            continue
        if cl.startswith("producer note (do not use as narration)"):
            continue
        return False
    return True


def resolve_chapter_narration_tts_body(chapter: Chapter, scenes: list[Any]) -> str | None:
    """
    Text to send to TTS: scene VO joined, else chapter script. Skips outline-only placeholders;
    if scenes hold placeholders but ``script_text`` is real, uses script.
    """
    script = (chapter.script_text or "").strip()
    parts = [(s.narration_text or "").strip() for s in scenes if (s.narration_text or "").strip()]
    if parts:
        from_scenes = "\n\n".join(parts)
        if not is_producer_only_chapter_summary_for_vo(from_scenes):
            return from_scenes if len(from_scenes) >= 8 else None
    if len(script) >= 8 and not is_producer_only_chapter_summary_for_vo(script):
        return script
    return None


# Seed image prompts: richer than a raw narration paste; still capped in ``build_scene_plan_batch``.
_IMAGE_PROMPT_BOILERPLATE = (
    "Cinematic documentary photograph, 16:9, one frozen moment in time, sharp focal subject, "
    "readable environment, natural light, photoreal, no typography or watermark on the image. "
    "Visual treatment: "
)

_DEFAULT_SCENE_NEGATIVE_PROMPT = (
    "text, watermark, logo, subtitles, UI, deformed anatomy, extra limbs, blurry, low resolution, "
    "oversaturated, cartoon, collage, split screen"
)

# Motion / camera brief for generative video and for coarse FFmpeg Ken Burns hints (local still→video).
_VIDEO_PROMPT_LEAD = (
    "Documentary clip motion: subtle slow push-in, observational handheld stability, shallow depth of field; "
    "maintain the same subject and setting as the scene still. Beat context: "
)


def chapter_eligible_for_scene_planning(chapter: Chapter, *, min_chars: int = MIN_CHARS_FOR_SCENE_PLANNING) -> bool:
    """True if chapter has enough script_text or a substantive (non-outline) summary for scene planning."""
    if len((chapter.script_text or "").strip()) >= min_chars:
        return True
    su = (chapter.summary or "").strip()
    return len(su) >= min_chars and not is_producer_only_chapter_summary_for_vo(su)


def chapter_eligible_for_scene_extend(
    chapter: Chapter, *, min_chars: int = MIN_CHARS_FOR_SCENE_PLANNING
) -> bool:
    """
    True if we may append another scene.

    Same gate as scene planning when starting from script/summary, **or** the chapter already has
    planned scenes whose narration + purpose text is enough for the extend LLM / deterministic
    fallback (so users are not blocked when script lives only inside scene beats).
    """
    if chapter_eligible_for_scene_planning(chapter, min_chars=min_chars):
        return True
    scenes = getattr(chapter, "scenes", None) or []
    if not scenes:
        return False
    total = 0
    for s in scenes:
        total += len((getattr(s, "narration_text", None) or "").strip())
        total += len((getattr(s, "purpose", None) or "").strip())
    # Require more than a single short token across all beats (2× planning floor).
    return total >= min_chars * 2


def _split_script_blocks(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    if not parts:
        parts = [raw]
    merged: list[str] = []
    for p in parts:
        if merged and len(p.split()) < 36:
            merged[-1] = f"{merged[-1]}\n\n{p}"
        else:
            merged.append(p)
    return merged[:48]


def _segment_text_for_beats(text: str) -> list[str]:
    """Split into clauses/sentences; several strategies so run-on scripts still produce 2+ segments."""
    t = (text or "").strip()
    if not t:
        return []
    for pat in (
        r"(?<=[.!?])\s+",
        r"(?<=[.!?;])\s+",
        r"\s*;\s+",
        r"\s*\n\s*",
    ):
        parts = [p.strip() for p in re.split(pat, t) if p and str(p).strip()]
        if len(parts) >= 2:
            return parts
    return [t]


def _word_chunks(text: str, n_chunks: int, *, min_words_per_chunk: int = 8) -> list[str] | None:
    words = [w for w in (text or "").split() if w]
    if n_chunks < 2 or len(words) < n_chunks * min_words_per_chunk:
        return None
    out: list[str] = []
    for i in range(n_chunks):
        start = int(round(i * len(words) / n_chunks))
        end = int(round((i + 1) * len(words) / n_chunks))
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            out.append(chunk)
    return out if len(out) >= 2 else None


def _split_single_block_to_beats(block: str, target_duration_sec: int | None) -> list[str]:
    """
    If a chapter comes as one long paragraph, split it into a few beat-sized blocks
    so scene planning doesn't collapse to a single scene.
    """
    raw = (block or "").strip()
    if not raw:
        return []
    word_n = len(raw.split())
    if word_n < 18:
        return [raw]
    est_sec = max(5, int(round(word_n / 130.0 * 60)))
    tsec = int(target_duration_sec or 0)
    # Use max(chapter target, estimated VO time) so short chapter targets (e.g. 60s) don't force one giant scene.
    t_eff = max(tsec, est_sec)
    sents = _segment_text_for_beats(raw)
    # ~45s of narration per beat, cap beats by segment count and a sane upper bound.
    target_beats = max(2, min(24, int(round(t_eff / 45.0))))
    if len(sents) < 2:
        wc = _word_chunks(raw, min(target_beats, max(2, word_n // 12)), min_words_per_chunk=8)
        return wc if wc else [raw]
    beats = min(target_beats, len(sents))
    if beats <= 1:
        return [raw]
    out: list[str] = []
    for i in range(beats):
        start = int(round(i * len(sents) / beats))
        end = int(round((i + 1) * len(sents) / beats))
        chunk = " ".join(sents[start:end]).strip()
        if chunk:
            out.append(chunk)
    return out or [raw]


def _expand_blocks_to_minimum(blocks: list[str], min_scenes: int) -> list[str]:
    """When workspace min scenes > len(blocks), split narration into at least min_scenes chunks."""
    m = max(1, min(48, int(min_scenes)))
    if m <= 1 or len(blocks) >= m:
        return blocks
    combined = "\n\n".join(b.strip() for b in blocks if (b or "").strip())
    if not combined.strip():
        return blocks
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", combined) if s.strip()]
    if len(sents) >= m:
        out: list[str] = []
        for i in range(m):
            start = int(round(i * len(sents) / m))
            end = int(round((i + 1) * len(sents) / m))
            chunk = " ".join(sents[start:end]).strip()
            if chunk:
                out.append(chunk)
        return out if len(out) >= m else blocks
    words_list = combined.split()
    wn = len(words_list)
    if wn < m:
        return blocks
    out_w: list[str] = []
    for i in range(m):
        start = int(round(i * wn / m))
        end = int(round((i + 1) * wn / m))
        chunk = " ".join(words_list[start:end]).strip()
        if chunk:
            out_w.append(chunk)
    return out_w if len(out_w) >= m else blocks


def _visual_type_for_project(project: Project, style_for_hints: str | None = None) -> str:
    s = (style_for_hints or project.visual_style or project.topic or "").lower()
    if any(k in s for k in ("archival", "historical", "photo", "document")):
        return "archival_still"
    if any(k in s for k in ("abstract", "motion", "graphic")):
        return "abstract_motion"
    return "b_roll"


def _embed_character_prefix_in_prompt_package(
    pp: dict[str, Any],
    character_consistency_prefix: str | None,
) -> None:
    """Prepend project character descriptions to stored image/video prompts (matches worker-time fusion)."""
    if not character_consistency_prefix or not str(character_consistency_prefix).strip():
        return
    pre = str(character_consistency_prefix).strip()
    for key, max_len in (("image_prompt", 4000), ("video_prompt", 3000)):
        cur = pp.get(key)
        if not isinstance(cur, str) or not cur.strip():
            continue
        base = cur.strip()
        if base.startswith(pre):
            continue
        room = max(200, max_len - len(pre) - 3)
        pp[key] = sanitize_jsonb_text(f"{pre}\n\n{base[:room]}", max_len)


def build_scene_plan_batch(
    chapter: Chapter,
    project: Project,
    *,
    visual_style_prompt: str | None = None,
    min_scenes: int = 0,
    scene_clip_duration_sec: int | None = None,
    character_consistency_prefix: str | None = None,
) -> dict[str, Any]:
    """Build scene-plan-batch/v1 from chapter script (no LLM)."""
    script = (chapter.script_text or "").strip()
    summary = (chapter.summary or "").strip()
    if not script:
        if not summary:
            raise ValueError(
                "CHAPTER_SCRIPT_REQUIRED: set chapter.script_text (phase 2 chapters) or chapter.summary before scene planning."
            )
        if is_producer_only_chapter_summary_for_vo(summary):
            raise ValueError(
                "CHAPTER_SCRIPT_REQUIRED: write full chapter script_text first — outline-only producer summaries are not narration."
            )
    blocks = _split_script_blocks(script) if script else []
    if not blocks and summary:
        if is_producer_only_chapter_summary_for_vo(summary):
            raise ValueError(
                "CHAPTER_SCRIPT_REQUIRED: chapter summary is outline metadata only; add script_text before scene planning."
            )
        blocks = [summary]
    if not blocks:
        raise ValueError(
            "CHAPTER_SCRIPT_REQUIRED: chapter script has no usable blocks after parsing; add substantive script_text."
        )
    if len(blocks) == 1:
        blocks = _split_single_block_to_beats(blocks[0], chapter.target_duration_sec)
    else:
        # Multiple paragraphs: still split any very long block (merged scripts, pasted acts, etc.).
        grown: list[str] = []
        for b in blocks:
            w = len((b or "").split())
            if w >= 48:
                grown.extend(_split_single_block_to_beats(b, chapter.target_duration_sec))
            else:
                grown.append(b.strip())
        blocks = [x for x in grown if x.strip()]
    try:
        floor = max(0, min(48, int(min_scenes)))
    except (TypeError, ValueError):
        floor = 0
    clip = int(scene_clip_duration_sec) if scene_clip_duration_sec in (5, 10) else 10
    wct_total = len(script.split()) if script else len(summary.split())
    # Match scene_plan_refine_context: enough VO → at least this many beats so the LLM cannot
    # replace a multi-beat seed with a single scene (common in auto mode).
    if wct_total >= 18:
        est_sec = max(5, int(round(wct_total / 130.0 * 60)))
        suggested = max(1, min(48, int(round(est_sec / float(clip)))))
    else:
        suggested = 1
    effective_min = max(floor, suggested)
    if effective_min > 1 and len(blocks) < effective_min:
        blocks = _expand_blocks_to_minimum(blocks, effective_min)

    resolved_visual = (visual_style_prompt or "").strip()
    if not resolved_visual:
        resolved_visual = (project.visual_style or "cinematic documentary natural light").strip()
    vtype = _visual_type_for_project(project, style_for_hints=resolved_visual)
    chapter_tag = sanitize_jsonb_text(chapter.title, 120)
    scenes: list[dict[str, Any]] = []
    for idx, narr in enumerate(blocks):
        narr_clean = sanitize_jsonb_text(narr, 12_000)
        words = max(1, len(narr_clean.split()))
        planned = max(5, min(600, int(round(words / 130.0 * 60))))
        purpose = sanitize_jsonb_text(narr_clean.replace("\n", " ")[:280], 300) or f"Beat {idx + 1}"
        # Cap style so long presets cannot consume the whole 4000-char budget before narration.
        _tail = ". What we see (from this beat): "
        _style_cap = max(120, 4000 - len(_IMAGE_PROMPT_BOILERPLATE) - len(_tail) - 1000)
        style = sanitize_jsonb_text(resolved_visual, min(3000, _style_cap))
        head = f"{_IMAGE_PROMPT_BOILERPLATE}{style}{_tail}"
        narr_room = max(200, 4000 - len(head))
        narr_excerpt = narr_clean[: min(1400, narr_room)]
        _vp_room = max(120, 3000 - len(_VIDEO_PROMPT_LEAD))
        pp: dict[str, Any] = {
            "image_prompt": sanitize_jsonb_text(head + narr_excerpt, 4000),
            "video_prompt": sanitize_jsonb_text(
                _VIDEO_PROMPT_LEAD + narr_clean[: min(900, _vp_room)],
                3000,
            ),
            "negative_prompt": _DEFAULT_SCENE_NEGATIVE_PROMPT,
            "chapter_title": chapter.title,
        }
        tags = [chapter_tag, f"scene_{idx + 1}", vtype]
        img_p = project.preferred_image_provider
        vid_p = project.preferred_video_provider
        _embed_character_prefix_in_prompt_package(pp, character_consistency_prefix)
        row: dict[str, Any] = {
            "order_index": idx,
            "purpose": purpose,
            "planned_duration_sec": planned,
            "narration_text": narr_clean,
            "visual_type": vtype,
            "prompt_package_json": pp,
            "continuity_tags_json": tags,
        }
        if img_p:
            row["preferred_image_provider"] = img_p
        if vid_p:
            row["preferred_video_provider"] = vid_p
        scenes.append(row)

    return {"schema_id": "scene-plan-batch/v1", "scenes": scenes}


def build_extend_scene_deterministic(
    chapter: Chapter,
    project: Project,
    *,
    prior_narrations: list[str],
    last_visual_type: str,
    visual_style_prompt: str,
    character_consistency_prefix: str | None = None,
) -> dict[str, Any]:
    """Single-scene plan when LLM refinement is skipped (e.g. agent_run_fast)."""
    script = (chapter.script_text or "").strip()
    summary = (chapter.summary or "").strip()
    merged_prior = "\n\n".join((x or "").strip() for x in prior_narrations if (x or "").strip()).strip()
    new_narr = ""
    if script:
        if merged_prior and merged_prior in script:
            new_narr = script.split(merged_prior, 1)[1].strip()
        else:
            last_para = (prior_narrations[-1] or "").strip() if prior_narrations else ""
            if last_para and last_para in script:
                idx = script.rfind(last_para)
                if idx >= 0:
                    new_narr = script[idx + len(last_para) :].strip()
        if not new_narr:
            paras = [p.strip() for p in script.split("\n\n") if p.strip()]
            if paras:
                new_narr = paras[-1][:1200]
    if not new_narr.strip() and summary:
        new_narr = summary[:800]
    if not new_narr.strip():
        topic = (project.topic or chapter.title or "this chapter").strip()
        new_narr = (
            f"Carrying the thread forward from the prior beat, we continue the account of {topic}, "
            "tightening focus on what comes next in the story."
        )
    new_narr = sanitize_jsonb_text(new_narr, 12_000)
    resolved_visual = (visual_style_prompt or "").strip()
    if not resolved_visual:
        resolved_visual = (project.visual_style or "cinematic documentary natural light").strip()
    vtype = (last_visual_type or "").strip() or _visual_type_for_project(project, style_for_hints=resolved_visual)
    _ext_tail = ". Seamless continuation of the prior beat. What we see (from this beat): "
    _style_cap_e = max(120, 4000 - len(_IMAGE_PROMPT_BOILERPLATE) - len(_ext_tail) - 1000)
    style = sanitize_jsonb_text(resolved_visual, min(3000, _style_cap_e))
    words = max(1, len(new_narr.split()))
    planned = max(5, min(600, int(round(words / 130.0 * 60))))
    purpose = sanitize_jsonb_text(new_narr.replace("\n", " ")[:280], 300) or "Extended beat"
    chapter_tag = sanitize_jsonb_text(chapter.title, 120)
    head = f"{_IMAGE_PROMPT_BOILERPLATE}{style}{_ext_tail}"
    narr_room = max(200, 4000 - len(head))
    narr_excerpt = new_narr[: min(1400, narr_room)]
    _vp_room_e = max(120, 3000 - len(_VIDEO_PROMPT_LEAD))
    pp: dict[str, Any] = {
        "image_prompt": sanitize_jsonb_text(head + narr_excerpt, 4000),
        "video_prompt": sanitize_jsonb_text(
            _VIDEO_PROMPT_LEAD + new_narr[: min(900, _vp_room_e)],
            3000,
        ),
        "negative_prompt": _DEFAULT_SCENE_NEGATIVE_PROMPT,
        "chapter_title": chapter.title,
    }
    _embed_character_prefix_in_prompt_package(pp, character_consistency_prefix)
    tags = [chapter_tag, "extended_scene", vtype]
    img_p = project.preferred_image_provider
    vid_p = project.preferred_video_provider
    row: dict[str, Any] = {
        "order_index": 0,
        "purpose": purpose,
        "planned_duration_sec": planned,
        "narration_text": new_narr,
        "visual_type": vtype,
        "prompt_package_json": pp,
        "continuity_tags_json": tags,
    }
    if img_p:
        row["preferred_image_provider"] = img_p
    if vid_p:
        row["preferred_video_provider"] = vid_p
    return {"schema_id": "scene-plan-batch/v1", "scenes": [row]}


def scene_plan_refine_context(chapter: Chapter, settings: Any) -> dict[str, Any]:
    """
    Production hints for the scene-plan agent: estimated VO length vs configured clip length,
    so the LLM can blend seed structure with a sensible scene count band.
    """
    script = (chapter.script_text or "").strip()
    words = max(0, len(script.split()))
    wpm = 130.0
    est_sec = max(5, int(round(words / wpm * 60))) if words else 5
    clip = int(getattr(settings, "scene_clip_duration_sec", None) or 10)
    if clip not in (5, 10):
        clip = 10
    try:
        target_fixed = int(getattr(settings, "scene_plan_target_scenes_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        target_fixed = 0
    target_fixed = max(0, min(48, target_fixed))
    auto_suggested = max(1, min(48, int(round(est_sec / float(clip)))))
    if target_fixed > 0:
        # User value is a floor; storyboard may add more scenes up to the usual cap.
        floor = target_fixed
        suggested = max(floor, auto_suggested)
        return {
            "scene_clip_duration_sec": clip,
            "estimated_narration_sec": est_sec,
            "suggested_scene_count": suggested,
            "scene_count_min": floor,
            "scene_count_max": 48,
            "word_count": words,
            "speaking_rate_wpm_assumption": 130,
        }
    suggested = auto_suggested
    margin = max(2, suggested // 4)
    return {
        "scene_clip_duration_sec": clip,
        "estimated_narration_sec": est_sec,
        "suggested_scene_count": suggested,
        "scene_count_min": max(1, suggested - margin),
        "scene_count_max": min(48, suggested + margin),
        "word_count": words,
        "speaking_rate_wpm_assumption": 130,
    }
