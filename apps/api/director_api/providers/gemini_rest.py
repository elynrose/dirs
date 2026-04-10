"""Google Gemini HTTP client (generateContent JSON) for LLM-style calls (Gemini API key, ai.google.dev)."""

from __future__ import annotations

import json
from typing import Any

import httpx

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def parse_gemini_usage_json(data: dict[str, Any]) -> dict[str, Any] | None:
    um = data.get("usageMetadata")
    if not isinstance(um, dict):
        return None
    try:
        pt = int(um.get("promptTokenCount") or 0)
        ct = int(um.get("candidatesTokenCount") or um.get("totalTokenCount") or 0)
        if ct == 0 and pt == 0:
            return None
        tt = int(um.get("totalTokenCount") or (pt + ct))
        return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
    except (TypeError, ValueError):
        return None


def gemini_generate_content_json(
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.35,
    timeout_sec: float = 120.0,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Call generateContent with JSON output. Returns (parsed_json, raw_response_dict, error)."""
    model_id = model.strip().lstrip("models/")
    url = f"{GEMINI_API_BASE}/models/{model_id}:generateContent"
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            r = client.post(url, params={"key": api_key}, json=body)
        if r.status_code >= 400:
            return None, None, f"gemini HTTP {r.status_code}: {(r.text or '')[:900]}"
        data = r.json()
        if not isinstance(data, dict):
            return None, None, "gemini response is not a JSON object"
        raw_text = _extract_text_from_generate_content(data)
        if not raw_text:
            return None, data, "gemini returned no text in candidates"
        t = _strip_json_markdown_fence(raw_text)
        try:
            parsed = json.loads(t)
        except Exception as e:  # noqa: BLE001
            return None, data, f"invalid JSON from gemini: {e}; start={raw_text[:200]!r}"
        if not isinstance(parsed, dict):
            return None, data, f"gemini JSON root is {type(parsed).__name__}, expected object"
        return parsed, data, None
    except httpx.HTTPError as e:
        return None, None, f"gemini request failed: {type(e).__name__}: {e}"[:800]


def _strip_json_markdown_fence(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip() in ("```", ""):
            lines.pop()
        t = "\n".join(lines).strip()
    return t


def _extract_text_from_generate_content(data: dict[str, Any]) -> str:
    cands = data.get("candidates") or []
    if not cands:
        return ""
    parts = ((cands[0] or {}).get("content") or {}).get("parts") or []
    chunks: list[str] = []
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            chunks.append(p["text"])
    return "".join(chunks).strip()
