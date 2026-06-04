"""Phase 2 job helpers — resolved lazily from ``worker_runtime`` to avoid import cycles."""

from __future__ import annotations

from typing import Any, Callable


def _bind(name: str) -> Callable[..., Any]:
    def _fn(*args: Any, **kwargs: Any) -> Any:
        from director_api.tasks import worker_runtime as wr

        return getattr(wr, name)(*args, **kwargs)

    _fn.__name__ = name
    _fn.__qualname__ = name
    return _fn


_characters_generate_core = _bind("_characters_generate_core")
_phase2_chapter_script_regenerate_core = _bind("_phase2_chapter_script_regenerate_core")
_phase2_chapters_core = _bind("_phase2_chapters_core")
_phase2_outline_core = _bind("_phase2_outline_core")
_phase2_research_core = _bind("_phase2_research_core")
