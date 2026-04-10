"""Tests for Phase 2 text-provider gate helpers in worker_tasks."""

from types import SimpleNamespace

import pytest

from director_api.tasks.worker_tasks import _active_text_llm_configured, _require_active_text_llm


def _base_ns(**overrides):
    d = {
        "active_text_provider": "openrouter",
        "openrouter_api_key": "rk",
        "openai_api_key": None,
        "openai_api_base_url": None,
        "openai_compatible_text_source": "openai",
        "lm_studio_api_base_url": None,
        "lm_studio_api_key": None,
        "lm_studio_text_model": "",
        "openai_smoke_model": "gpt-4o-mini",
        "xai_api_key": None,
        "grok_api_key": None,
        "gemini_api_key": None,
    }
    d.update(overrides)
    return SimpleNamespace(**d)


def test_active_text_llm_configured_openrouter_with_key() -> None:
    assert _active_text_llm_configured(_base_ns()) is True


def test_active_text_llm_configured_openrouter_missing_key() -> None:
    assert _active_text_llm_configured(_base_ns(openrouter_api_key=None)) is False


def test_require_active_text_llm_passes_when_configured() -> None:
    _require_active_text_llm(_base_ns(), for_what="unit test")


def test_require_active_text_llm_raises_when_not_configured() -> None:
    with pytest.raises(ValueError, match="not fully configured"):
        _require_active_text_llm(_base_ns(openrouter_api_key=None), for_what="unit test")


def test_active_text_llm_gemini_requires_key() -> None:
    assert _active_text_llm_configured(_base_ns(active_text_provider="gemini", gemini_api_key="g")) is True
    assert _active_text_llm_configured(_base_ns(active_text_provider="google", gemini_api_key=None)) is False
