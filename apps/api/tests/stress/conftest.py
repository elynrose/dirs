"""Shared markers and env helpers for stress tests."""

from __future__ import annotations

import os


def stress_integration_enabled() -> bool:
    return os.environ.get("STRESS_INTEGRATION", "").strip().lower() in ("1", "true", "yes")


def stress_scene_count(default: int = 80) -> int:
    raw = os.environ.get("STRESS_SCENE_COUNT", "").strip()
    if not raw:
        return default
    try:
        return max(5, min(int(raw), 500))
    except ValueError:
        return default


def stress_http_base_url() -> str:
    return os.environ.get("STRESS_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
