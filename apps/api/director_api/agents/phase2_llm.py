"""Optional OpenAI-backed Phase 2 agents. All outputs validated with JSON Schema at call site."""

from __future__ import annotations

import json
from typing import Any

import httpx

from director_api.agents.json_from_model import (
    parse_model_json_loose,
    raw_assistant_text_for_json,
)
from director_api.agents.openai_client import (
    make_openai_client,
    openai_chat_targets_local_compatible_server,
    openai_compatible_configured,
    resolve_openai_compatible_chat_model,
)
from director_api.config import Settings
from director_api.llm_prompt_catalog import PROMPT_DEFAULTS
from director_api.services.llm_prompt_runtime import get_llm_prompt_text
from director_api.services.research_service import sanitize_jsonb_text
from director_api.services.usage_accounting import (
    append_llm_usage_sink,
    parse_openai_chat_usage,
    parse_openrouter_usage_json,
)


_LOCAL_OPENAI_JSON_SUFFIX = (
    " Output discipline (required): emit the full JSON object in the assistant's normal message content "
    "(the field API clients surface as assistant text). Do not leave that content empty or place the JSON only "
    "in extended/internal reasoning; Directely parses the visible assistant payload as JSON."
)


def _system_for_openai_json_local(settings: Settings, system: str) -> str:
    if openai_chat_targets_local_compatible_server(settings):
        return system + _LOCAL_OPENAI_JSON_SUFFIX
    return system


def _normalize_text_provider(settings: Settings) -> str:
    provider = str(getattr(settings, "active_text_provider", "openai")).strip().lower()
    if provider in ("", "default", "auto"):
        return "openai"
    if provider == "google":
        return "gemini"
    return provider


def resolve_workspace_text_chat_model(settings: Settings) -> tuple[str, str]:
    """(provider_key, model_id) for the active text provider — same values as Settings UI / app_settings."""
    prov = _normalize_text_provider(settings)
    if prov == "openrouter":
        return prov, str(settings.openrouter_smoke_model or "openai/gpt-4o-mini").strip()
    if prov in ("xai", "grok"):
        return prov, str(settings.xai_text_model or "grok-2-latest").strip()
    if prov == "gemini":
        return prov, str(getattr(settings, "gemini_text_model", None) or "gemini-2.0-flash").strip()
    if prov == "lm_studio":
        return "lm_studio", resolve_openai_compatible_chat_model(settings)
    return "openai", resolve_openai_compatible_chat_model(settings)


def _chat_json_object_ex(
    settings: Settings,
    *,
    system: str,
    user: str,
    service_type: str,
    usage_sink: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Call chat completions expecting a single JSON object; return (data, error_message)."""
    provider = _normalize_text_provider(settings)
    _, chat_model = resolve_workspace_text_chat_model(settings)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        if provider == "openrouter":
            if not settings.openrouter_api_key:
                return None, "OPENROUTER_API_KEY is not set (active text provider is openrouter)"
            url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
            last_http = ""
            with httpx.Client(timeout=float(settings.openai_timeout_sec)) as client:
                for use_rf in (True, False):
                    payload: dict[str, Any] = {
                        "model": chat_model,
                        "temperature": float(temperature if temperature is not None else 0.35),
                        "messages": messages,
                    }
                    if use_rf:
                        payload["response_format"] = {"type": "json_object"}
                    resp = client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {settings.openrouter_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    if resp.status_code >= 400:
                        last_http = f"openrouter HTTP {resp.status_code}: {(resp.text or '')[:700]}"
                        if use_rf:
                            continue
                        return None, last_http
                    body_json = resp.json()
                    if not isinstance(body_json, dict):
                        return None, "openrouter response body is not a JSON object"
                    u = parse_openrouter_usage_json(body_json)
                    append_llm_usage_sink(
                        usage_sink,
                        provider="openrouter",
                        model=chat_model,
                        service_type=service_type,
                        usage=u,
                    )
                    msg = (body_json.get("choices") or [{}])[0].get("message") or {}
                    raw = (
                        (msg.get("content") or "").strip()
                        or (msg.get("reasoning_content") or "").strip()
                        or (msg.get("reasoning") or "").strip()
                    )
                    out, perr = parse_model_json_loose(raw)
                    if out is not None:
                        return out, None
                    return None, perr or "could not parse model JSON"
            return None, last_http or "openrouter request failed"

        if provider == "gemini":
            key = getattr(settings, "gemini_api_key", None)
            if not key:
                return None, "GEMINI_API_KEY is not set (active text provider is gemini)"
            from director_api.providers.gemini_rest import gemini_generate_content_json, parse_gemini_usage_json

            temp = float(temperature if temperature is not None else 0.35)
            parsed, raw_data, err = gemini_generate_content_json(
                api_key=str(key),
                model=chat_model,
                system=system,
                user=user,
                temperature=temp,
                timeout_sec=float(settings.openai_timeout_sec),
            )
            if err:
                return None, err
            if not isinstance(parsed, dict):
                return None, "gemini returned non-object JSON"
            u = parse_gemini_usage_json(raw_data) if isinstance(raw_data, dict) else None
            append_llm_usage_sink(
                usage_sink,
                provider="gemini",
                model=chat_model,
                service_type=service_type,
                usage=u,
            )
            return parsed, None

        if provider in ("xai", "grok"):
            xai_key = getattr(settings, "xai_api_key", None) or getattr(settings, "grok_api_key", None)
            if not xai_key:
                return None, "XAI_API_KEY or GROK_API_KEY is not set (active text provider is xai/grok)"
            from openai import OpenAI

            client = OpenAI(
                api_key=xai_key,
                base_url=settings.xai_base_url,
                timeout=float(settings.openai_timeout_sec),
            )
            try:
                r = client.chat.completions.create(
                    model=chat_model,
                    temperature=float(temperature if temperature is not None else 0.35),
                    response_format={"type": "json_object"},
                    messages=messages,
                )
            except Exception as e:  # noqa: BLE001
                return None, f"xai/grok chat failed: {type(e).__name__}: {e}"[:800]
            u = parse_openai_chat_usage(r)
            append_llm_usage_sink(
                usage_sink,
                provider="xai",
                model=chat_model,
                service_type=service_type,
                usage=u,
            )
            raw = raw_assistant_text_for_json(r.choices[0].message)
            return parse_model_json_loose(raw)

        if not openai_compatible_configured(settings):
            if provider == "lm_studio":
                return None, "LM_STUDIO_API_BASE_URL is required (active text provider is lm_studio)"
            return None, "OPENAI_API_KEY or compatible endpoint is required (active text provider is openai)"

        client = make_openai_client(settings)
        local = openai_chat_targets_local_compatible_server(settings)
        sys_eff = _system_for_openai_json_local(settings, system)
        messages_eff = [
            {"role": "system", "content": sys_eff},
            {"role": "user", "content": user},
        ]
        base_kw: dict[str, Any] = {
            "model": chat_model,
            "temperature": float(temperature if temperature is not None else 0.35),
            "messages": messages_eff,
        }
        if local:
            base_kw["max_tokens"] = int(settings.openai_local_chat_max_tokens)

        usage_provider = "lm_studio" if provider == "lm_studio" else "openai"

        def _finish(r: Any) -> tuple[dict[str, Any] | None, str | None]:
            u = parse_openai_chat_usage(r)
            append_llm_usage_sink(
                usage_sink,
                provider=usage_provider,
                model=chat_model,
                service_type=service_type,
                usage=u,
            )
            raw = raw_assistant_text_for_json(r.choices[0].message)
            return parse_model_json_loose(raw)

        if local:
            last_http = ""
            for use_rf in (True, False):
                kw = dict(base_kw)
                if use_rf:
                    kw["response_format"] = {"type": "json_object"}
                try:
                    r = client.chat.completions.create(**kw)
                except Exception as e:  # noqa: BLE001
                    err_s = str(e).lower()
                    last_http = f"openai chat failed: {type(e).__name__}: {e}"[:800]
                    if use_rf and any(
                        x in err_s for x in ("response_format", "json_object", "json mode", "unsupported")
                    ):
                        continue
                    return None, last_http
                out, perr = _finish(r)
                if out is not None:
                    return out, None
                if use_rf:
                    last_http = perr or "could not parse model JSON"
                    continue
                return None, perr or last_http or "could not parse model JSON"
            return None, last_http or "openai local chat failed"

        try:
            r = client.chat.completions.create(
                **base_kw,
                response_format={"type": "json_object"},
            )
        except Exception as e:  # noqa: BLE001
            return None, f"openai chat failed: {type(e).__name__}: {e}"[:800]
        return _finish(r)
    except Exception as e:  # noqa: BLE001
        return None, f"LLM request failed ({provider}): {type(e).__name__}: {e}"[:800]


def _chat_json_object(
    settings: Settings,
    *,
    system: str,
    user: str,
    service_type: str,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    data, _err = _chat_json_object_ex(
        settings, system=system, user=user, service_type=service_type, usage_sink=usage_sink
    )
    return data


def enrich_director_pack(
    pack: dict[str, Any],
    project_title: str,
    topic: str,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return validated-shaped director pack; on failure return input."""
    sys = get_llm_prompt_text("phase2_director_enrich")
    user = json.dumps(
        {
            "seed_pack": pack,
            "title": project_title,
            "topic": topic,
        },
        ensure_ascii=False,
    )
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase2_director_enrich", usage_sink=usage_sink
    )
    if not out or out.get("schema_id") != "director-pack/v1":
        return pack
    out["title"] = sanitize_jsonb_text(str(out.get("title") or project_title), 500)
    out["topic"] = sanitize_jsonb_text(str(out.get("topic") or topic), 8000)
    if not isinstance(out.get("narrative_arc"), list):
        return pack
    return out


def enrich_research_dossier_body(
    body: dict[str, Any],
    *,
    topic: str,
    sources_preview: list[dict[str, Any]],
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add fact_graph + chapter_evidence_packs when possible."""
    sys = get_llm_prompt_text("phase2_research_enrich")
    user = json.dumps(
        {"draft": body, "topic": topic, "sources": sources_preview[:12]},
        ensure_ascii=False,
    )
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase2_research_enrich", usage_sink=usage_sink
    )
    if not out:
        return body
    merged = {**body}
    if isinstance(out.get("summary"), str):
        merged["summary"] = sanitize_jsonb_text(out["summary"], 12000)
    if isinstance(out.get("timeline"), list) and out["timeline"]:
        merged["timeline"] = out["timeline"]
    if isinstance(out.get("fact_graph"), dict):
        merged["fact_graph"] = out["fact_graph"]
    if isinstance(out.get("chapter_evidence_packs"), list):
        merged["chapter_evidence_packs"] = out["chapter_evidence_packs"]
    return merged


def generate_outline_batch(
    *,
    director: dict[str, Any],
    dossier: dict[str, Any],
    target_runtime_minutes: int,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    total_sec = max(300, target_runtime_minutes * 60)
    tpl = get_llm_prompt_text("phase2_outline_batch")
    try:
        sys = tpl.format(total_sec=total_sec)
    except (KeyError, ValueError):
        sys = PROMPT_DEFAULTS["phase2_outline_batch"].format(total_sec=total_sec)
    user = json.dumps(
        {"director": director, "dossier": dossier, "target_runtime_minutes": target_runtime_minutes},
        ensure_ascii=False,
    )
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase2_outline_batch", usage_sink=usage_sink
    )
    if not out or out.get("schema_id") != "chapter-outline-batch/v1":
        return None
    return out


def generate_scripts_batch(
    *,
    director: dict[str, Any],
    dossier: dict[str, Any],
    chapters: list[dict[str, Any]],
    allowed_claims: list[str],
    disputed_claims: list[str],
    settings: Settings,
    narration_style: str | None = None,
    tone: str | None = None,
    audience: str | None = None,
    target_scenes_per_chapter: int = 0,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    tsp = max(0, min(48, int(target_scenes_per_chapter or 0)))
    read_shape = (
        "Write flowing paragraphs suitable for a single VO read. "
        if tsp == 0
        else (
            f"Each script_text must contain exactly {tsp} paragraphs separated by a blank line (empty line). "
            f"One paragraph = one narrative beat that maps to one downstream scene; count must match target_scene_count "
            f"on each chapter ({tsp}). Do not number beats or add headings—only normal documentary prose. "
            "Each paragraph should be substantive VO; spread words across beats. "
        )
    )
    scene_budget = ""
    if tsp > 0:
        scene_budget = (
            f"MANDATORY scene budget: every script_text must have exactly {tsp} blank-line-separated paragraphs "
            "(no fewer, no more). The pipeline validates paragraph count. "
        )
    sys = (
        get_llm_prompt_text("phase2_scripts_batch_prefix")
        + read_shape
        + scene_budget
        + get_llm_prompt_text("phase2_scripts_batch_suffix")
    )
    user_obj: dict[str, Any] = {
        "director": director,
        "dossier_summary": dossier.get("summary"),
        "chapters": chapters,
        "allowed_claims": allowed_claims[:40],
        "disputed_claims": disputed_claims[:20],
        "brief_voice": {
            "narration_style": narration_style,
            "tone": tone,
            "audience": audience,
        },
    }
    if tsp > 0:
        user_obj["script_production"] = {
            "target_scenes_per_chapter": tsp,
            "beat_delimiter": "Blank line between paragraphs; each paragraph is one scene beat.",
        }
    order_indices = [int(c["order_index"]) for c in chapters]
    user_obj["required_order_indices"] = order_indices
    sys = (
        sys
        + "\n\nREQUIRED: scripts must include exactly one entry per chapter in user.chapters, "
        f"with order_index in {order_indices!s} (same set as required_order_indices). "
        "Do not omit a chapter. Each script_text must be substantive VO (meet min_words)."
    )
    user = json.dumps(user_obj, ensure_ascii=False)
    out = _chat_json_object(
        settings, system=sys, user=user, service_type="phase2_scripts_batch", usage_sink=usage_sink
    )
    if not out or out.get("schema_id") != "chapter-scripts-batch/v1":
        return None
    # Some models emit explicit null for optional transition_to_next; schema expects string when present.
    if isinstance(out.get("scripts"), list):
        for s in out["scripts"]:
            if isinstance(s, dict) and s.get("transition_to_next") is None:
                s.pop("transition_to_next", None)
    return out


def regenerate_chapter_script_llm(
    *,
    director: dict[str, Any],
    dossier_summary: Any,
    chapter_title: str,
    order_index: int,
    current_script: str,
    enhancement_notes: str,
    target_duration_sec: int,
    allowed_claims: list[str],
    disputed_claims: list[str],
    settings: Settings,
    narration_style: str | None = None,
    tone: str | None = None,
    audience: str | None = None,
    target_scenes_per_chapter: int = 0,
    usage_sink: list[dict[str, Any]] | None = None,
) -> str | None:
    """Return revised script_text or None. Output schema chapter-script-revise/v1."""
    from director_api.services import phase2 as phase2_svc

    tsp = max(0, min(48, int(target_scenes_per_chapter or 0)))
    tsec = max(30, min(7200, int(target_duration_sec or 120)))
    tw = phase2_svc.target_narration_word_count(tsec)
    min_words = max(80, int(tw * 0.78))
    read_shape = (
        "Write flowing paragraphs suitable for a single VO read. "
        if tsp == 0
        else (
            f"script_text must contain exactly {tsp} paragraphs separated by a blank line (empty line). "
            f"One paragraph = one narrative beat / scene; count must be {tsp}. "
            "Do not number beats or add headings—only normal documentary prose. "
        )
    )
    scene_budget = ""
    if tsp > 0:
        scene_budget = (
            f"MANDATORY scene budget: script_text must have exactly {tsp} blank-line-separated paragraphs "
            "(no fewer, no more). The pipeline validates paragraph count. "
        )
    sys = get_llm_prompt_text("phase2_chapter_script_revise") + read_shape + scene_budget
    user_obj: dict[str, Any] = {
        "director": director,
        "dossier_summary": dossier_summary,
        "chapter": {
            "order_index": order_index,
            "title": chapter_title,
            "target_duration_sec": tsec,
            "target_words_approx": tw,
            "min_words": min_words,
        },
        "current_script": (current_script or "")[:120_000],
        "enhancement_notes": (enhancement_notes or "")[:16_000],
        "allowed_claims": allowed_claims[:40],
        "disputed_claims": disputed_claims[:20],
        "brief_voice": {
            "narration_style": narration_style,
            "tone": tone,
            "audience": audience,
        },
    }
    if tsp > 0:
        user_obj["script_production"] = {
            "target_scenes_per_chapter": tsp,
            "beat_delimiter": "Blank line between paragraphs; each paragraph is one scene beat.",
        }
    user = json.dumps(user_obj, ensure_ascii=False)
    out = _chat_json_object(
        settings,
        system=sys,
        user=user,
        service_type="phase2_chapter_script_regenerate",
        usage_sink=usage_sink,
    )
    if not out or out.get("schema_id") != "chapter-script-revise/v1":
        return None
    st = out.get("script_text")
    if not isinstance(st, str) or not st.strip():
        return None
    return st.strip()


def _trim_character_bible_payload(
    chapters_context: list[dict[str, Any]],
    *,
    max_chapters: int = 8,
    max_field_chars: int = 3500,
) -> list[dict[str, Any]]:
    """Keep prompts within context limits; long scripts caused empty/truncated model JSON."""
    out: list[dict[str, Any]] = []
    for c in chapters_context[:max_chapters]:
        if not isinstance(c, dict):
            continue
        row = dict(c)
        for key in ("script_excerpt", "chapter_summary"):
            v = row.get(key)
            if isinstance(v, str) and len(v) > max_field_chars:
                row[key] = v[:max_field_chars] + "…"
        out.append(row)
    return out


def generate_character_bible(
    *,
    director: dict[str, Any],
    chapters_context: list[dict[str, Any]],
    project_title: str,
    project_topic: str,
    dossier_summary: str | None,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (character-bible/v1 dict, None) or (None, user-facing error)."""
    _, model_label = resolve_workspace_text_chat_model(settings)
    sys = get_llm_prompt_text("phase2_character_bible")
    chapters_trim = _trim_character_bible_payload(chapters_context)
    user_obj: dict[str, Any] = {
        "project_title": sanitize_jsonb_text(project_title, 500),
        "project_topic": sanitize_jsonb_text(project_topic, 4000),
        "program_director_brief": director,
        "chapters_context": chapters_trim,
        "dossier_summary": (dossier_summary or "")[:6000] or None,
    }
    user = json.dumps(user_obj, ensure_ascii=False)

    def _call(u: str) -> tuple[dict[str, Any] | None, str | None]:
        return _chat_json_object_ex(
            settings,
            system=sys,
            user=u,
            service_type="character_bible",
            usage_sink=usage_sink,
        )

    out, llm_err = _call(user)
    if llm_err:
        return None, llm_err
    if not out:
        return None, "empty response from text provider"
    if out.get("schema_id") != "character-bible/v1":
        repair = (
            f"{user}\n\n"
            "CRITICAL FIX: Your previous JSON used the wrong schema. Respond again with ONLY valid JSON: "
            '{"schema_id":"character-bible/v1","characters":[...]} '
            "The characters array is required. Do not use schema_id director-pack/v1 or any other value."
        )
        out2, err2 = _call(repair)
        if err2:
            return None, err2
        out = out2
    if not out or out.get("schema_id") != "character-bible/v1":
        got = (out or {}).get("schema_id")
        return None, (
            f"model returned schema_id={got!r} but character-bible/v1 is required "
            f"(active text model: {model_label!r}). "
            "Use a JSON-mode-capable model in Settings for your active text provider, or retry."
        )
    return out, None
