"""Detect optional FFmpeg filters (e.g. drawtext requires freetype)."""

from __future__ import annotations

import re
import subprocess


def ffmpeg_filter_available(
    ffmpeg_bin: str,
    filter_name: str,
    *,
    timeout_sec: float = 12.0,
) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    return re.search(rf"\b{re.escape(filter_name)}\b", blob) is not None
