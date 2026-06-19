"""LLM helpers for publish pack (thumbnail metadata, opening hook, outro CTA)."""

from __future__ import annotations

import json
from typing import Any

from director_api.agents.phase2_llm import _chat_json_object_ex
from director_api.config import Settings
from director_api.services.llm_prompt_runtime import get_llm_prompt_text
from director_api.services.research_service import sanitize_jsonb_text


def generate_publish_thumbnail_pack_llm(
    *,
    project_title: str,
    project_topic: str,
    chapter_titles: list[str],
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, str] | None:
    """Return {youtube_title, youtube_description, thumbnail_prompt} or None on failure."""
    system = get_llm_prompt_text("publish_thumbnail_pack")
    user = json.dumps(
        {
            "title": project_title,
            "topic": project_topic,
            "chapter_titles": chapter_titles[:12],
        },
        ensure_ascii=False,
    )
    data, err = _chat_json_object_ex(
        settings,
        system=system,
        user=user,
        service_type="publish_thumbnail_pack",
        usage_sink=usage_sink,
        temperature=0.55,
    )
    if err or not data:
        return None
    title = sanitize_jsonb_text(str(data.get("youtube_title") or project_title), 100)
    desc = sanitize_jsonb_text(str(data.get("youtube_description") or project_topic), 5000)
    prompt = sanitize_jsonb_text(str(data.get("thumbnail_prompt") or ""), 2000)
    if not title or not prompt:
        return None
    return {
        "youtube_title": title,
        "youtube_description": desc,
        "thumbnail_prompt": prompt,
    }


def generate_opening_hook_llm(
    *,
    project_title: str,
    project_topic: str,
    director_pack: dict[str, Any] | None,
    dossier_summary: str,
    first_chapter_excerpt: str,
    narration_style: str,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> str | None:
    system = get_llm_prompt_text("opening_hook_script")
    user = json.dumps(
        {
            "title": project_title,
            "topic": project_topic,
            "director_pack": director_pack or {},
            "dossier_summary": dossier_summary[:4000],
            "first_chapter_excerpt": first_chapter_excerpt[:2500],
            "narration_style": narration_style,
        },
        ensure_ascii=False,
    )
    data, err = _chat_json_object_ex(
        settings,
        system=system,
        user=user,
        service_type="opening_hook_script",
        usage_sink=usage_sink,
        temperature=0.65,
    )
    if err or not data:
        return None
    hook = sanitize_jsonb_text(str(data.get("hook_script") or data.get("opening_hook") or ""), 8000)
    return hook if len(hook.strip()) >= 20 else None


def generate_outro_cta_llm(
    *,
    project_title: str,
    narration_style: str,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> str | None:
    system = get_llm_prompt_text("outro_cta_script")
    user = json.dumps(
        {"title": project_title, "narration_style": narration_style},
        ensure_ascii=False,
    )
    data, err = _chat_json_object_ex(
        settings,
        system=system,
        user=user,
        service_type="outro_cta_script",
        usage_sink=usage_sink,
        temperature=0.5,
    )
    if err or not data:
        return None
    text = sanitize_jsonb_text(str(data.get("narration_text") or data.get("outro_script") or ""), 4000)
    return text if len(text.strip()) >= 12 else None
