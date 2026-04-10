"""Encode a short black full-frame clip with centered title text (H.264 + silent AAC)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.nt_staging import (
    audio_should_use_short_temp,
    copy_short_to_destination,
    make_short_concat_staging_dir,
)
from ffmpeg_pipelines.overlay_video import _sanitize_drawtext
from ffmpeg_pipelines.paths import ffmpeg_argv_path, mkdir_parent, path_is_readable_file, path_stat


def encode_black_title_card_mp4(
    output_path: Path,
    *,
    title: str,
    duration_sec: float = 2.5,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    crf: int = 23,
    preset: str = "veryfast",
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 120.0,
) -> dict[str, Any]:
    """Solid black video with white ``title`` centered; matches still_to_video output layout for concat."""
    output_path = output_path.resolve()
    dur = max(0.4, min(float(duration_sec), 60.0))
    text = _sanitize_drawtext(title, max_len=120)
    vf = (
        f"format=yuv420p,"
        f"drawtext=text='{text}':fontsize=52:fontcolor=white:borderw=2:bordercolor=black@0.6:"
        f"x=(w-text_w)/2:y=(h-text_h)/2"
    )
    st_root: Path | None = None
    out_write = output_path
    try:
        if os.name == "nt" and audio_should_use_short_temp([output_path]):
            st_root = make_short_concat_staging_dir()
            out_write = st_root / "card.mp4"
        else:
            mkdir_parent(output_path)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r={fps}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{dur:.3f}",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-shortest",
            ffmpeg_argv_path(out_write),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-4000:]
            raise FFmpegCompileError(tail.strip() or "black_title_card failed")
        if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 32:
            raise FFmpegCompileError("black_title_card produced empty output")
        if st_root is not None:
            copy_short_to_destination(out_write, output_path)
        if not path_is_readable_file(output_path) or path_stat(output_path).st_size < 32:
            raise FFmpegCompileError("black_title_card produced empty output")
        return {
            "output_path": str(output_path),
            "bytes": path_stat(output_path).st_size,
            "duration_sec": dur,
            "mode": "black_title_card",
        }
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
