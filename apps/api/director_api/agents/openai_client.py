"""Factory for OpenAI SDK clients: official API, Azure/custom base, or LM Studio (separate settings)."""

from __future__ import annotations

from typing import Any

from director_api.config import Settings


def normalize_openai_base_url_for_sdk(raw: str | None) -> str | None:
    """Return ``base_url`` for the OpenAI Python SDK (must end with ``/v1``), or None."""
    s = (raw or "").strip()
    if not s:
        return None
    u = s.rstrip("/")
    if not u.endswith("/v1"):
        u = f"{u}/v1"
    return u


def openai_compatible_text_source_normalized(settings: Settings) -> str:
    v = str(getattr(settings, "openai_compatible_text_source", "openai") or "openai").strip().lower()
    return "lm_studio" if v == "lm_studio" else "openai"


def active_text_provider_is_lm_studio(settings: Settings) -> bool:
    """True when Generation uses LM Studio as the text provider (not only the OpenAI/LM routing toggle)."""
    p = str(getattr(settings, "active_text_provider", "openai") or "openai").strip().lower()
    return p == "lm_studio"


def _use_lm_studio_openai_host(settings: Settings) -> bool:
    """LM Studio base URL + keys apply (either text provider or OpenAI-tab routing)."""
    return active_text_provider_is_lm_studio(settings) or openai_compatible_text_source_normalized(settings) == "lm_studio"


def effective_openai_compatible_base_url(settings: Settings) -> str | None:
    """Which OpenAI-compatible host chat/completions use (or None for default api.openai.com)."""
    if _use_lm_studio_openai_host(settings):
        return normalize_openai_base_url_for_sdk(getattr(settings, "lm_studio_api_base_url", None))
    return normalize_openai_base_url_for_sdk(getattr(settings, "openai_api_base_url", None))


def resolve_openai_compatible_chat_model(settings: Settings) -> str:
    """Chat model id for the active OpenAI-compatible text source."""
    if _use_lm_studio_openai_host(settings):
        lm = (getattr(settings, "lm_studio_text_model", None) or "").strip()
        if lm:
            return lm
    return str(settings.openai_smoke_model or "gpt-4o-mini").strip()


def openai_sdk_connection_kwargs(settings: Settings) -> dict[str, Any]:
    """Arguments for ``openai.OpenAI`` / ``openai.AsyncOpenAI``."""
    timeout = float(settings.openai_timeout_sec)
    base = effective_openai_compatible_base_url(settings)
    if _use_lm_studio_openai_host(settings):
        key = (
            (getattr(settings, "lm_studio_api_key", None) or "").strip()
            or (settings.openai_api_key or "").strip()
            or "lm-studio"
        )
        if base:
            return {"api_key": key, "base_url": base, "timeout": timeout}
        return {"api_key": (settings.openai_api_key or "").strip(), "timeout": timeout}
    key = (settings.openai_api_key or "").strip()
    if base:
        return {
            "api_key": key or "lm-studio",
            "base_url": base,
            "timeout": timeout,
        }
    return {"api_key": key, "timeout": timeout}


def openai_compatible_configured(settings: Settings) -> bool:
    """True when chat/completions via the OpenAI client can run."""
    if _use_lm_studio_openai_host(settings):
        return bool(effective_openai_compatible_base_url(settings))
    if (settings.openai_api_key or "").strip():
        return True
    return bool(effective_openai_compatible_base_url(settings))


def openai_chat_targets_local_compatible_server(settings: Settings) -> bool:
    """True when the OpenAI SDK uses a custom base URL (LM Studio, vLLM, Azure host, etc.)."""
    return bool(effective_openai_compatible_base_url(settings))


def make_openai_client(settings: Settings):
    from openai import OpenAI

    return OpenAI(**openai_sdk_connection_kwargs(settings))


def make_async_openai_client(settings: Settings):
    from openai import AsyncOpenAI

    return AsyncOpenAI(**openai_sdk_connection_kwargs(settings))
