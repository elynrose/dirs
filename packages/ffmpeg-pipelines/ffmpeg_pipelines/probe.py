"""ffprobe helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ffmpeg_pipelines.paths import ffmpeg_argv_path, path_is_readable_file


def ffprobe_duration_seconds(
    media_path: Path,
    *,
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 60.0,
) -> float:
    """Probe the duration of a media file in seconds.

    Requests both ``format=duration`` and ``stream=duration`` in one pass so that
    files whose container-level duration is "N/A" (common for certain FAL/CogVideo
    outputs and files produced without proper muxer moov-atom updates) still return
    a valid value from the first stream that carries timing information.
    """
    path = media_path.resolve()
    if not path_is_readable_file(path):
        raise FileNotFoundError(path)
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        ffmpeg_argv_path(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ffprobe failed")[:2000])
    # Output is one value per line: format duration first, then per-stream durations.
    # Take the first line that is a parseable positive float — "N/A" lines are skipped.
    for raw_line in (proc.stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line == "N/A":
            continue
        try:
            val = float(line)
        except ValueError:
            continue
        if val > 0:
            return val
    raise RuntimeError(f"ffprobe could not determine a positive duration for: {path}")
