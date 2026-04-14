"""LLM helpers to improve image retry prompts and scene VO with project context."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.agents.json_from_model import parse_model_json_content, raw_assistant_text_for_json
from director_api.agents.openai_client import (
    make_openai_client,
    openai_chat_targets_local_compatible_server,
    openai_compatible_configured,
    resolve_openai_compatible_chat_model,
)
from director_api.config import Settings
from director_api.db.models import Chapter, Project, Scene
from director_api.services.character_prompt import character_bible_for_llm_context
from director_api.style_presets import effective_narration_style


def _previous_scene_in_chapter(db: Session, sc: Scene) -> Scene | None:
    return db.scalar(
        select(Scene)
        .where(Scene.chapter_id == sc.chapter_id)
        .where(Scene.order_index < sc.order_index)
        .order_by(Scene.order_index.desc())
        .limit(1)
    )


def _summarize_previous_scene(prev: Scene) -> str:
    parts: list[str] = []
    if prev.purpose and str(prev.purpose).strip():
        parts.append(f"Purpose: {str(prev.purpose).strip()[:1200]}")
    if prev.narration_text and str(prev.narration_text).strip():
        parts.append(f"Prior VO / narration: {str(prev.narration_text).strip()[:2000]}")
    pp = prev.prompt_package_json if isinstance(prev.prompt_package_json, dict) else {}
    im = pp.get("image_prompt") if isinstance(pp, dict) else None
    if isinstance(im, str) and im.strip():
        parts.append(f"Prior image prompt (from package): {im.strip()[:1500]}")
    if not parts:
        return "No prior scene in this chapter, or the previous beat has no text yet."
    return "\n".join(parts)


def _chat_json_text_field(
    settings: Settings,
    *,
    system: str,
    user: str,
    field: str = "text",
    max_out_tokens: int = 4096,
) -> tuple[str | None, str | None]:
    """Ask the model for a JSON object `{field: "..."}`. Returns (value, error)."""
    if not openai_compatible_configured(settings):
        return None, "Text generation is not configured (set OPENAI_API_KEY or LM Studio base URL)."

    client = make_openai_client(settings)
    model = resolve_openai_compatible_chat_model(settings)
    local = openai_chat_targets_local_compatible_server(settings)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    kw: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.35,
        "max_tokens": min(max_out_tokens, int(getattr(settings, "openai_local_chat_max_tokens", 8192) or 8192)),
    }
    if not local:
        kw["response_format"] = {"type": "json_object"}

    try:
        if local:
            last_err = ""
            for use_rf in (True, False):
                attempt_kw = dict(kw)
                if use_rf:
                    attempt_kw["response_format"] = {"type": "json_object"}
                try:
                    r = client.chat.completions.create(**attempt_kw)
                except Exception as e:  # noqa: BLE001
                    err_s = str(e).lower()
                    last_err = f"{type(e).__name__}: {e}"[:800]
                    if use_rf and any(x in err_s for x in ("response_format", "json_object", "json mode", "unsupported")):
                        continue
                    return None, last_err
                raw = raw_assistant_text_for_json(r.choices[0].message)
                parsed, perr = parse_model_json_content(raw)
                if parsed and isinstance(parsed.get(field), str) and parsed[field].strip():
                    return parsed[field].strip(), None
                if use_rf:
                    last_err = perr or "could not parse JSON"
                    continue
                return None, perr or last_err or "empty model output"
            return None, last_err or "local LLM prompt enhance failed"
        r = client.chat.completions.create(**kw)
        raw = raw_assistant_text_for_json(r.choices[0].message)
        parsed, perr = parse_model_json_content(raw)
        if parsed and isinstance(parsed.get(field), str) and parsed[field].strip():
            return parsed[field].strip(), None
        return None, perr or "could not parse improved text from model"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"[:800]


def enhance_image_retry_prompt(
    db: Session,
    settings: Settings,
    *,
    scene_id: uuid.UUID,
    current_prompt: str,
) -> tuple[str | None, str | None]:
    sc = db.get(Scene, scene_id)
    if not sc:
        return None, "scene not found"
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        return None, "chapter not found"
    proj = db.get(Project, ch.project_id)
    if not proj or proj.tenant_id != settings.default_tenant_id:
        return None, "project not found"

    prev = _previous_scene_in_chapter(db, sc)
    prev_block = _summarize_previous_scene(prev) if prev else "This is the first scene in the chapter — no prior beat."
    bible = character_bible_for_llm_context(db, proj.id, max_chars=6000)
    if not bible.strip():
        bible = "(No project character bible yet — infer generic documentary consistency.)"

    system = (
        "You improve image-generation prompts for documentary/factual video stills. "
        "Rewrite the user's CURRENT PROMPT so it stays faithful to their intent but integrates: "
        "(1) continuity with the PREVIOUS SCENE summary when relevant, "
        "(2) character/visual consistency from the CHARACTER BIBLE when relevant. "
        "Keep a single fluent English prompt suitable for an image model (no markdown, no bullet labels). "
        f'Respond with a JSON object only: {{"text": "<improved prompt>"}}.'
    )
    user = (
        f"Project title: {proj.title}\n"
        f"Project topic: {str(proj.topic or '')[:1500]}\n\n"
        f"CHARACTER BIBLE:\n{bible}\n\n"
        f"PREVIOUS SCENE (same chapter):\n{prev_block}\n\n"
        f"CURRENT PROMPT TO IMPROVE:\n{current_prompt.strip()[:4000]}"
    )
    return _chat_json_text_field(settings, system=system, user=user, field="text", max_out_tokens=2048)


def refine_bracket_visual_prompt_llm(
    db: Session,
    settings: Settings,
    *,
    scene_id: uuid.UUID,
    draft_prompt: str,
    bracket_phrases: list[str],
    narration_excerpt: str | None = None,
) -> tuple[str | None, str | None]:
    """Optional LLM pass: merge ``[bracket]`` hints into one precise still prompt. User must opt in at job time."""
    sc = db.get(Scene, scene_id)
    if not sc:
        return None, "scene not found"
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        return None, "chapter not found"
    proj = db.get(Project, ch.project_id)
    if not proj or proj.tenant_id != settings.default_tenant_id:
        return None, "project not found"

    prev = _previous_scene_in_chapter(db, sc)
    prev_block = _summarize_previous_scene(prev) if prev else "This is the first scene in the chapter — no prior beat."
    bible = character_bible_for_llm_context(db, proj.id, max_chars=4000)
    if not bible.strip():
        bible = "(No project character bible yet — infer generic documentary consistency.)"

    hints = "; ".join(bracket_phrases[:16])
    narr_ctx = (narration_excerpt or sc.narration_text or "")[:4000]

    system = (
        "The user marked visual emphasis in their scene narration using [bracketed] phrases. "
        "You must produce ONE fluent English prompt for a photoreal documentary still image generator. "
        "Merge ALL bracket hints into a single coherent scene (same world state, one moment in time). "
        "Match the project's visual style when given. Do not paste voice-over script; describe only what the camera sees. "
        "No markdown, no bullet labels. "
        f'Respond with JSON only: {{"text": "<single image prompt>"}}.'
    )
    user = (
        f"Project title: {proj.title}\n"
        f"Project topic: {str(proj.topic or '')[:1500]}\n\n"
        f"Scene purpose: {str(sc.purpose or '')[:1200]}\n\n"
        f"CHARACTER BIBLE (consistency):\n{bible}\n\n"
        f"PREVIOUS SCENE (continuity):\n{prev_block}\n\n"
        f"USER BRACKET HINTS (must all inform the still):\n{hints}\n\n"
        f"DRAFT VISUAL PROMPT (improve or replace; keep intent):\n{draft_prompt.strip()[:3500]}\n\n"
        f"FULL NARRATION (context only — do not quote verbatim):\n{narr_ctx}"
    )
    return _chat_json_text_field(settings, system=system, user=user, field="text", max_out_tokens=2048)


def enhance_scene_vo_script(
    db: Session,
    settings: Settings,
    *,
    scene_id: uuid.UUID,
    current_script: str,
    narration_style_prompt_override: str | None = None,
) -> tuple[str | None, str | None]:
    sc = db.get(Scene, scene_id)
    if not sc:
        return None, "scene not found"
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        return None, "chapter not found"
    proj = db.get(Project, ch.project_id)
    if not proj or proj.tenant_id != settings.default_tenant_id:
        return None, "project not found"

    style_raw = (narration_style_prompt_override or "").strip()
    if not style_raw:
        style_raw = effective_narration_style(
            proj.narration_style,
            settings,
            db=db,
            tenant_id=proj.tenant_id,
        ).strip()
    if not style_raw:
        return None, "narration style could not be resolved — set the project's narration style or pass narration_style_prompt."

    system = (
        "You rewrite spoken voice-over narration for documentary or factual programs. "
        "Apply the NARRATION STYLE instructions precisely — they may describe tone, pacing, rhetorical structure "
        '(e.g. "ask a question then answer it"), or other delivery constraints. '
        "Preserve facts and meaning from the CURRENT SCRIPT unless the style requires light restructuring; "
        "do not invent new factual claims. "
        'Respond with JSON only: {"text": "<rewritten narration>"}.'
    )
    user = (
        f"Project title: {proj.title}\n"
        f"Scene purpose: {str(sc.purpose or '')[:1200]}\n\n"
        f"NARRATION STYLE (follow this):\n{style_raw[:3500]}\n\n"
        f"CURRENT SCRIPT:\n{current_script.strip()[:12000]}"
    )
    return _chat_json_text_field(settings, system=system, user=user, field="text", max_out_tokens=8192)


def expand_scene_vo_script(
    db: Session,
    settings: Settings,
    *,
    scene_id: uuid.UUID,
    current_script: str,
    target_sentence_count: int,
    expansion_context: str | None = None,
    narration_style_prompt_override: str | None = None,
) -> tuple[str | None, str | None]:
    """Lengthen scene VO to ~N sentences; optional user context for what to add or stress."""
    sc = db.get(Scene, scene_id)
    if not sc:
        return None, "scene not found"
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        return None, "chapter not found"
    proj = db.get(Project, ch.project_id)
    if not proj or proj.tenant_id != settings.default_tenant_id:
        return None, "project not found"

    n = max(1, min(40, int(target_sentence_count)))

    style_raw = (narration_style_prompt_override or "").strip()
    if not style_raw:
        style_raw = effective_narration_style(
            proj.narration_style,
            settings,
            db=db,
            tenant_id=proj.tenant_id,
        ).strip()
    if not style_raw:
        style_raw = (
            "Spoken documentary voice-over: clear, natural read-aloud; third person unless the topic calls for direct address."
        )

    ctx = (expansion_context or "").strip()
    ctx_block = f"USER EXPANSION NOTES (follow if compatible with facts):\n{ctx[:2000]}\n\n" if ctx else ""

    system = (
        "You expand spoken documentary voice-over narration. "
        f"The expanded script should contain approximately {n} complete sentences "
        "(real sentence boundaries—avoid one endless comma chain). "
        "Preserve the facts and core meaning of the CURRENT SCRIPT; you may add detail, examples, transitions, "
        "or clarification that fits the topic—do not invent new factual claims or names not implied by the script or notes. "
        "Apply NARRATION STYLE for tone and pacing. "
        "If USER EXPANSION NOTES are present, weave them in where they fit; if they conflict with facts, prefer the script. "
        'Respond with JSON only: {"text": "<expanded narration>"}.'
    )
    user = (
        f"Project title: {proj.title}\n"
        f"Project topic: {str(proj.topic or '')[:1500]}\n"
        f"Chapter title: {str(ch.title or '')[:500]}\n\n"
        f"Scene purpose: {str(sc.purpose or '')[:1200]}\n\n"
        f"NARRATION STYLE:\n{style_raw[:3500]}\n\n"
        f"{ctx_block}"
        f"CURRENT SCRIPT:\n{current_script.strip()[:12000]}"
    )
    return _chat_json_text_field(settings, system=system, user=user, field="text", max_out_tokens=8192)
