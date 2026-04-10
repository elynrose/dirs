"""Write a short silent AAC clip (stereo 48 kHz) for mux / concat alignment."""

from __future__ import annotations

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
from ffmpeg_pipelines.paths import ffmpeg_argv_path, mkdir_parent, path_is_readable_file, path_stat


def write_silence_aac(
    output_path: Path,
    *,
    duration_sec: float,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    output_path = output_path.resolve()
    # Long silence segments are needed for scene-aligned final-cut stems (full programs).
    dur = max(0.05, min(float(duration_sec), 7200.0))

    # On Windows, FFmpeg does not accept \\?\-prefixed paths in command-line arguments.
    # When the output path is long, write to a short temp path and move afterwards.
    st_root: Path | None = None
    out_write = output_path
    try:
        if audio_should_use_short_temp([output_path]):
            st_root = make_short_concat_staging_dir()
            out_write = st_root / "silence.aac"
        else:
            mkdir_parent(output_path)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{dur:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            ffmpeg_argv_path(out_write),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-4000:]
            raise FFmpegCompileError(tail.strip() or "silence_aac failed")
        if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 16:
            raise FFmpegCompileError("silence_aac produced empty output")

        if st_root is not None:
            copy_short_to_destination(out_write, output_path)

        return {
            "output_path": str(output_path),
            "bytes": path_stat(output_path).st_size,
            "duration_sec": dur,
        }
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
