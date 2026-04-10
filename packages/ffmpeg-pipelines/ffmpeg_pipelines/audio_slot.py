"""Trim/pad a single audio file to an exact duration (stereo 48 kHz AAC)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.nt_staging import (
    audio_should_use_short_temp,
    copy_short_to_destination,
    make_short_concat_staging_dir,
    stage_inputs_as_hardlink_or_copy,
)
from ffmpeg_pipelines.paths import ffmpeg_argv_path, mkdir_parent, path_is_readable_file, path_stat


def normalize_audio_to_duration(
    input_path: Path,
    output_path: Path,
    duration_sec: float,
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 180.0,
) -> None:
    """Re-encode audio to exactly ``duration_sec`` (trim then pad with silence)."""
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if not path_is_readable_file(input_path):
        raise FFmpegCompileError(f"audio input not found: {input_path}")

    d = max(0.05, min(float(duration_sec), 7200.0))
    dstr = f"{d:.3f}"
    af = (
        "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000,"
        f"atrim=0:{dstr},asetpts=PTS-STARTPTS,apad=whole_dur={dstr}"
    )

    # On Windows, FFmpeg does not accept \\?\-prefixed paths. Stage long paths under %TEMP%.
    st_root: Path | None = None
    in_use = input_path
    out_write = output_path
    try:
        if audio_should_use_short_temp([input_path, output_path]):
            st_root = make_short_concat_staging_dir()
            in_use = stage_inputs_as_hardlink_or_copy([input_path], st_root)[0]
            out_write = st_root / "out.m4a"
        else:
            mkdir_parent(output_path)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            ffmpeg_argv_path(in_use),
            "-af",
            af,
            "-t",
            dstr,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            ffmpeg_argv_path(out_write),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-4000:]
            raise FFmpegCompileError(tail.strip() or "normalize_audio_to_duration failed")
        if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 32:
            raise FFmpegCompileError("normalize_audio_to_duration produced empty output")

        if st_root is not None:
            copy_short_to_destination(out_write, output_path)
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
