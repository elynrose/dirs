"""Best-effort FFmpeg version string for export manifests."""

from __future__ import annotations

import subprocess


def ffmpeg_version_line(ffmpeg_bin: str = "ffmpeg", *, timeout_sec: float = 5.0) -> str | None:
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-version"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout.strip().split("\n", 1)[0].strip()
