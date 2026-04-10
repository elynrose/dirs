"""LLM-backed project setup guide for Chat Studio (structured JSON reply)."""

from __future__ import annotations

import json
from typing import Any

from director_api.agents.phase2_llm import _chat_json_object_ex
from director_api.config import Settings
from director_api.style_presets import style_presets_public_payload

_SERVICE = "chat_studio_setup_guide"

# Keys allowed in brief_patch (subset of documentary brief + ProjectPatch).
_BRIEF_PATCH_KEYS = frozenset(
    {
        "title",
        "topic",
        "target_runtime_minutes",
        "audience",
        "tone",
        "visual_style",
        "narration_style",
        "factual_strictness",
        "music_preference",
        "research_min_sources",
        "preferred_text_provider",
        "preferred_image_provider",
        "preferred_video_provider",
        "preferred_speech_provider",
    }
)

_CHARACTER_KEYS = frozenset({"name", "role_in_story", "visual_description", "time_place_scope_notes"})

_PIPELINE_OVERVIEW = """Hands-off pipeline (unattended, through full_video): director pack → web research & dossier →
outline → chapter scripts → scene plans → character bible → scene images → scene videos (optional) → narration (TTS) → timeline →
rough cut → final mix / export. Interactive modes can stop earlier; Chat Studio always runs the full hands-off path.
Research scripts: chapter scripts and per-scene lines are grounded in the research dossier; thin sources yield weaker scripts.
"""


def _flatten_conversation(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = (m.get("role") or "").strip().lower()
        content = (m.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        parts.append(f"{label}: {content}")
    return "\n\n".join(parts)


def _sanitize_brief_patch(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in _BRIEF_PATCH_KEYS or v is None:
            continue
        if k == "target_runtime_minutes":
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if 2 <= n <= 120:
                out[k] = n
            continue
        if k == "research_min_sources":
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= 100:
                out[k] = n
            continue
        if k == "factual_strictness":
            if v in ("strict", "balanced", "creative"):
                out[k] = v
            continue
        if k in ("title", "topic", "audience", "tone", "music_preference"):
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()[:8000] if k != "title" else v.strip()[:500]
            continue
        if k in ("narration_style", "visual_style"):
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()[:8000]
            continue
        if k.startswith("preferred_") and k.endswith("_provider"):
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()[:200]
    return out


def _sanitize_character_drafts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for ck in _CHARACTER_KEYS:
            val = item.get(ck)
            if val is None:
                if ck == "time_place_scope_notes":
                    row[ck] = None
                continue
            if not isinstance(val, str):
                continue
            v = val.strip()
            if ck == "name" and (not v or len(v) > 256):
                continue
            if ck == "role_in_story" and len(v) > 2000:
                v = v[:2000]
            if ck == "visual_description" and len(v) > 8000:
                v = v[:8000]
            if ck == "time_place_scope_notes" and len(v) > 2000:
                v = v[:2000]
            row[ck] = v if ck != "time_place_scope_notes" or v else None
        if row.get("name"):
            if "role_in_story" not in row:
                row["role_in_story"] = ""
            if "visual_description" not in row:
                row["visual_description"] = ""
            out.append(row)
    return out


def _build_system_prompt(*, style_catalog: dict[str, Any], brief_snapshot: dict[str, Any]) -> str:
    cat_json = json.dumps(style_catalog, ensure_ascii=False)[:24000]
    snap_json = json.dumps(brief_snapshot, ensure_ascii=False)[:12000]
    return f"""You are Directely's Chat Studio setup guide. Help the user shape a documentary project before they run
the hands-off pipeline. Be concise and practical; ask one or two focused questions when something important is missing.

{_PIPELINE_OVERVIEW}

Style catalog (use narration_style as preset:<id> or user:<uuid> for custom workspace styles; visual_style as preset:<id>):
{cat_json}

Current brief snapshot (merge updates; do not invent facts):
{snap_json}

You MUST respond with a single JSON object (no markdown) using these keys:
- "reply" (string, required): what the user reads — questions, confirmations, short rationale.
- "brief_patch" (object, optional): only fields you are confident about; omit the key if unsure. Allowed keys:
  title, topic, target_runtime_minutes (2–120), audience, tone, narration_style, visual_style,
  factual_strictness (strict|balanced|creative), music_preference, research_min_sources (1–100),
  preferred_text_provider, preferred_image_provider, preferred_video_provider, preferred_speech_provider.
- "character_drafts" (array, optional): up to 8 entries with name, role_in_story, visual_description,
  time_place_scope_notes (string or null). Use when characters would help consistency.
- "notes_for_user" (string, optional): very short checklist (e.g. what to clarify next, or "Ready to generate.").

Stay factual; do not promise real research results. Prefer preset narration/visual ids from the catalog when matching tone."""


def run_setup_guide_turn(
    settings: Settings,
    *,
    messages: list[dict[str, str]],
    brief_snapshot: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (response_dict, error). response_dict keys: reply, brief_patch, character_drafts, notes_for_user."""
    if not messages:
        return None, "no messages"
    flat = _flatten_conversation(messages)
    if not flat.strip():
        return None, "empty conversation"
    style_catalog = style_presets_public_payload()
    system = _build_system_prompt(style_catalog=style_catalog, brief_snapshot=brief_snapshot)
    data, err = _chat_json_object_ex(
        settings,
        system=system,
        user=flat,
        service_type=_SERVICE,
        usage_sink=None,
        temperature=0.4,
    )
    if data is None:
        return None, err or "model returned no JSON"

    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None, "model JSON missing non-empty reply string"

    out: dict[str, Any] = {
        "reply": reply.strip()[:16000],
        "brief_patch": _sanitize_brief_patch(data.get("brief_patch")),
        "character_drafts": _sanitize_character_drafts(data.get("character_drafts")),
    }
    nu = data.get("notes_for_user")
    if isinstance(nu, str) and nu.strip():
        out["notes_for_user"] = nu.strip()[:4000]
    return out, None
