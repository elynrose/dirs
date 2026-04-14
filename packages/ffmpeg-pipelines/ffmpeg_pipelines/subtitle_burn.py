"""Optional WebVTT burn-in after final mux (soft subtitles → hard-coded pixels)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.paths import ffmpeg_argv_path, mkdir_parent, path_is_readable_file, path_stat


def burn_webvtt_subtitles_into_mp4(
    video_in: Path,
    vtt_in: Path,
    video_out: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 7200.0,
) -> dict[str, Any]:
    """
    Re-encode video with subtitles filter. Audio streams are copied when possible.

    ``vtt_in`` must be UTF-8 WebVTT readable by FFmpeg's ``subtitles`` filter.
    """
    video_in = video_in.resolve()
    vtt_in = vtt_in.resolve()
    video_out = video_out.resolve()
    if not path_is_readable_file(video_in):
        raise FFmpegCompileError(f"video not found: {video_in}")
    if not path_is_readable_file(vtt_in):
        raise FFmpegCompileError(f"subtitles not found: {vtt_in}")
    mkdir_parent(video_out)
    tmp = video_out.with_suffix(".burning.tmp.mp4")
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
    # Escape path for ffmpeg filter on Windows (backslashes, colons)
    sub_path = str(vtt_in).replace("\\", "/").replace(":", r"\:")
    vf = f"subtitles={sub_path}"
    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        ffmpeg_argv_path(video_in),
        "-vf",
        vf,
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        ffmpeg_argv_path(tmp),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-5000:]
        try:
            tmp.unlink(missing_ok=True)
        except TypeError:
            if tmp.exists():
                tmp.unlink()
        raise FFmpegCompileError(tail.strip() or "ffmpeg subtitle burn failed")
    if not path_is_readable_file(tmp) or path_stat(tmp).st_size < 64:
        try:
            tmp.unlink(missing_ok=True)
        except TypeError:
            if tmp.exists():
                tmp.unlink()
        raise FFmpegCompileError("subtitle burn produced empty output")
    shutil.move(str(tmp), str(video_out))
    return {
        "output_path": str(video_out),
        "bytes": path_stat(video_out).st_size,
        "mode": "burn_webvtt",
    }
