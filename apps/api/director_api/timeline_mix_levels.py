"""Read ``mix_*_volume`` from ``timeline_json`` without treating 0 as falsy."""

from __future__ import annotations

from typing import Any


def mix_music_volume_from_timeline(tj: dict[str, Any], *, default: float = 0.28) -> float:
    raw = tj.get("mix_music_volume")
    if raw is None:
        v = default
    else:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = default
    return max(0.0, min(1.0, v))


def mix_narration_volume_from_timeline(tj: dict[str, Any], *, default: float = 1.0) -> float:
    raw = tj.get("mix_narration_volume")
    if raw is None:
        v = default
    else:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = default
    return max(0.0, min(4.0, v))
