"""Request/task-scoped resolved LLM prompts (contextvar)."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from director_api.llm_prompt_catalog import PROMPT_DEFAULTS

_llm_resolved_map: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "llm_resolved_map", default=None
)


def get_llm_prompt_text(prompt_key: str) -> str:
    """Return effective prompt text for ``prompt_key`` (override → active map → built-in default)."""
    m = _llm_resolved_map.get()
    if m is not None:
        v = m.get(prompt_key)
        if isinstance(v, str) and v.strip():
            return v
    return PROMPT_DEFAULTS.get(
        prompt_key,
        "You are a helpful assistant. Return valid JSON only when requested.",
    )


@contextmanager
def llm_prompt_map_scope(resolved: dict[str, str]) -> Iterator[None]:
    tok = _llm_resolved_map.set(dict(resolved))
    try:
        yield
    finally:
        _llm_resolved_map.reset(tok)


