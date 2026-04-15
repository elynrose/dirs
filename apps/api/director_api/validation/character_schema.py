import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

_CHARACTER_MAX_ITEMS = 16
_NAME_MAX = 256
_ROLE_MAX = 2000
_VISUAL_MAX = 4000
_SCOPE_MAX = 2000


@lru_cache
def _character_bible_schema() -> dict[str, Any]:
    p = _REPO_ROOT / "packages" / "schemas" / "json" / "character-bible.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len]


def _coerce_sort_order(raw: Any, fallback: int) -> int:
    """JSON numbers may arrive as str from some providers; bool is rejected."""
    if raw is None:
        return fallback
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, float):
        return max(0, int(raw))
    if isinstance(raw, str):
        t = raw.strip()
        if not t:
            return fallback
        try:
            return max(0, int(float(t)))
        except (TypeError, ValueError):
            return fallback
    return fallback


def normalize_character_bible_llm_output(instance: dict[str, Any]) -> dict[str, Any]:
    """Strip unknown keys, coerce types, and clamp string lengths before JSON Schema validation.

    LLMs often add extra properties; our schema uses ``additionalProperties: false`` on each character,
    which would otherwise reject otherwise usable payloads.
    """
    if not isinstance(instance, dict):
        raise ValueError("character bible must be a JSON object")
    chars_in = instance.get("characters")
    if not isinstance(chars_in, list):
        raise ValueError("character bible must include a characters array")
    out_rows: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for idx, c in enumerate(chars_in[:_CHARACTER_MAX_ITEMS]):
        if not isinstance(c, dict):
            raise ValueError(f"characters[{idx}] must be an object")
        so = _coerce_sort_order(c.get("sort_order"), idx)
        name = _truncate(str(c.get("name") or "").strip(), _NAME_MAX)
        role = _truncate(str(c.get("role_in_story") or "").strip(), _ROLE_MAX)
        visual = _truncate(str(c.get("visual_description") or "").strip(), _VISUAL_MAX)
        slim: dict[str, Any] = {
            "sort_order": so,
            "name": name,
            "role_in_story": role,
            "visual_description": visual,
        }
        tp = c.get("time_place_scope_notes")
        if isinstance(tp, str) and tp.strip():
            slim["time_place_scope_notes"] = _truncate(tp.strip(), _SCOPE_MAX)
        out_rows.append(((so, idx), slim))
    out_rows.sort(key=lambda x: x[0])
    ordered = [row[1] for row in out_rows]
    return {"schema_id": "character-bible/v1", "characters": ordered}


def _format_character_bible_validation_error(err: jsonschema.ValidationError) -> str:
    path = ".".join(str(p) for p in err.absolute_path) if err.absolute_path else "(root)"
    return f"character bible JSON did not match schema at {path}: {err.message}"


def validate_character_bible_batch(instance: dict[str, Any]) -> dict[str, Any]:
    """Normalize, validate against the packaged schema, and return the normalized payload."""
    normalized = normalize_character_bible_llm_output(instance)
    try:
        jsonschema.validate(instance=normalized, schema=_character_bible_schema())
    except jsonschema.ValidationError as e:
        raise ValueError(_format_character_bible_validation_error(e)) from e
    return normalized
