"""Gemini API smoke test (text JSON)."""

from __future__ import annotations

from typing import Any

from director_api.config import Settings
from director_api.providers.gemini_rest import gemini_generate_content_json


def smoke_llm(settings: Settings) -> dict[str, Any]:
    key = getattr(settings, "gemini_api_key", None)
    if not key:
        return {"configured": False, "error": "GEMINI_API_KEY not set"}
    model = (getattr(settings, "gemini_text_model", None) or "gemini-2.0-flash").strip()
    parsed, _raw, err = gemini_generate_content_json(
        api_key=str(key),
        model=model,
        system='Reply with only a JSON object: {"ok":true}',
        user="ping",
        temperature=0.0,
        timeout_sec=45.0,
    )
    if err:
        return {"configured": False, "error": err[:1200], "model": model}
    if isinstance(parsed, dict) and parsed.get("ok") is True:
        return {"configured": True, "model": model, "sample": parsed}
    return {"configured": True, "model": model, "sample": parsed}
