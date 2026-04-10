"""Local placeholder scene images (no fal / ComfyUI) — solid frame via FFmpeg lavfi."""

from __future__ import annotations

import subprocess
from pathlib import Path


def render_placeholder_scene_png_bytes(
    *,
    ffmpeg_bin: str = "ffmpeg",
    width: int = 1280,
    height: int = 720,
    timeout_sec: float = 60.0,
) -> bytes:
    """Return a single PNG frame (16:9 friendly) for pipeline smoke tests."""
    ff = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    w = max(16, min(4096, int(width)))
    h = max(16, min(4096, int(height)))
    # Muted blue-gray slate; readable in Studio as “not real art”.
    spec = f"color=c=0x3a4f66:s={w}x{h}:d=1"
    cmd = [
        ff,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        spec,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec)
    if r.returncode != 0 or not r.stdout or len(r.stdout) < 32:
        err = (r.stderr or b"").decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"placeholder PNG ffmpeg failed ({r.returncode}): {err}")
    return bytes(r.stdout)


def write_placeholder_png_file(path: Path, **kwargs: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = render_placeholder_scene_png_bytes(**kwargs)
    path.write_bytes(data)
