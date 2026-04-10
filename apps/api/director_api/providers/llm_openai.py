"""OpenAI adapter — Tier A (`LLMProvider` smoke). SDK isolated here (P1-R01)."""

from typing import Any

from director_api.agents.json_from_model import raw_assistant_text_for_json
from director_api.agents.openai_client import (
    make_openai_client,
    openai_compatible_configured,
    resolve_openai_compatible_chat_model,
)
from director_api.config import Settings


def smoke_llm(settings: Settings) -> dict[str, Any]:
    if not openai_compatible_configured(settings):
        return {
            "configured": False,
            "provider": "openai",
            "error": "OpenAI text is not configured (API key and/or LM Studio base URL)",
        }

    client = make_openai_client(settings)
    chat_model = resolve_openai_compatible_chat_model(settings)
    # Reasoning models (e.g. Qwen3 in LM Studio) may fill `reasoning_*` before `content`; keep headroom.
    resp = client.chat.completions.create(
        model=chat_model,
        messages=[{"role": "user", "content": "Reply with exactly: ok"}],
        max_tokens=256,
    )
    text = raw_assistant_text_for_json(resp.choices[0].message).strip()
    return {
        "configured": True,
        "provider": "openai",
        "model": chat_model,
        "output": text,
    }
