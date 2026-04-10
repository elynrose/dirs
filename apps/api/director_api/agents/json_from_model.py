"""Parse JSON objects from LLM assistant text (code fences, Qwen-style reasoning + trailing JSON)."""

from __future__ import annotations

import json
from typing import Any


def parse_model_json_content(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a single JSON object from model output; strip ``` fences if present."""
    t = (raw or "").strip()
    if not t:
        return None, "model returned empty message content"
    if t.startswith("```"):
        lines = t.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip() in ("```", ""):
            lines.pop()
        t = "\n".join(lines).strip()
    try:
        out = json.loads(t)
    except json.JSONDecodeError as e:
        frag = t[:320].replace("\n", " ")
        return None, f"invalid JSON from model: {e}; start={frag!r}"
    if not isinstance(out, dict):
        return None, f"model JSON root is {type(out).__name__}, expected object"
    return out, None


def top_level_brace_spans(text: str) -> list[str]:
    """Return substrings that are balanced {...} groups by naive brace depth (good enough for typical model JSON)."""
    s = text or ""
    n = len(s)
    out: list[str] = []
    i = 0
    while i < n:
        if s[i] != "{":
            i += 1
            continue
        depth = 0
        start = i
        j = i
        while j < n:
            ch = s[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out.append(s[start : j + 1])
                    i = j + 1
                    break
            j += 1
        else:
            i += 1
    return out


def parse_model_json_loose(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Like parse_model_json_content, then scan for embedded top-level JSON objects (e.g. after reasoning prose)."""
    data, err = parse_model_json_content(raw)
    if data is not None:
        return data, None
    last_err = err
    for span in reversed(top_level_brace_spans(raw)):
        data2, err2 = parse_model_json_content(span)
        if data2 is not None:
            return data2, None
        last_err = err2 or last_err
    return None, last_err


def raw_assistant_text_for_json(message: Any) -> str:
    """Visible assistant text for JSON parsing: prefer content; fall back to reasoning fields (Qwen3 / LM Studio)."""
    c = getattr(message, "content", None)
    if isinstance(c, str) and c.strip():
        return c.strip()
    for attr in ("reasoning_content", "reasoning", "thinking"):
        r = getattr(message, attr, None)
        if isinstance(r, str) and r.strip():
            return r.strip()
    return ""
