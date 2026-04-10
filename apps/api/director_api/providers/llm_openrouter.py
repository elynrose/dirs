"""OpenRouter adapter — Tier A (OpenAI-compatible HTTP)."""

from typing import Any

import httpx

from director_api.config import Settings


def smoke_llm(settings: Settings) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        return {"configured": False, "provider": "openrouter", "error": "OPENROUTER_API_KEY not set"}

    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.openrouter_smoke_model,
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 16,
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if r.status_code >= 400:
        return {
            "configured": True,
            "provider": "openrouter",
            "error": f"http_{r.status_code}",
            "body": r.text[:500],
        }
    data = r.json()
    text = ""
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        text = str(data)[:500]
    return {
        "configured": True,
        "provider": "openrouter",
        "model": settings.openrouter_smoke_model,
        "output": text,
    }
