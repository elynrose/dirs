"""Concatenate multiple audio files into one (stereo 48 kHz, AAC)."""

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
    stage_inputs_as_hardlink_or_copy,
)
from ffmpeg_pipelines.paths import ffmpeg_argv_path, mkdir_parent, path_is_readable_file, path_stat


def concat_audio_files(
    paths: list[Path],
    output_path: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 900.0,
) -> dict[str, Any]:
    """
    Join audio streams in order. Normalizes each input to stereo fltp 48 kHz before ``concat``.
    """
    if not paths:
        raise FFmpegCompileError("no audio inputs")
    for p in paths:
        if not path_is_readable_file(p):
            raise FFmpegCompileError(f"audio input not found: {p}")

    output_path = output_path.resolve()

    # On Windows, FFmpeg does not accept \\?\-prefixed paths in argv. Stage all inputs and
    # the output under a short %TEMP% directory when any path exceeds the safe threshold.
    st_root: Path | None = None
    paths_use: list[Path]
    out_write: Path
    try:
        if audio_should_use_short_temp([*paths, output_path]):
            st_root = make_short_concat_staging_dir()
            paths_use = stage_inputs_as_hardlink_or_copy([p.resolve() for p in paths], st_root)
            out_write = st_root / "out.m4a"
        else:
            paths_use = [p.resolve() for p in paths]
            out_write = output_path
            mkdir_parent(output_path)

        n = len(paths_use)
        if n == 1:
            # Re-encode to a consistent format so downstream mux is predictable.
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                ffmpeg_argv_path(paths_use[0]),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                ffmpeg_argv_path(out_write),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "")[-4000:]
                raise FFmpegCompileError(tail.strip() or "ffmpeg audio copy failed")
            if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 32:
                raise FFmpegCompileError("ffmpeg produced empty audio output")
            if st_root is not None:
                copy_short_to_destination(out_write, output_path)
            return {
                "output_path": str(output_path),
                "bytes": path_stat(output_path).st_size,
                "input_count": 1,
                "mode": "audio_single_reencode",
            }

        args: list[str] = [ffmpeg_bin, "-y"]
        for p in paths_use:
            args.extend(["-i", ffmpeg_argv_path(p)])

        fmt = "sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000"
        norm = [f"[{i}:a]aformat={fmt},asetpts=PTS-STARTPTS[a{i}]" for i in range(n)]
        concat_ins = "".join(f"[a{i}]" for i in range(n))
        concat = f"{concat_ins}concat=n={n}:v=0:a=1[outa]"
        filter_complex = ";".join(norm) + ";" + concat

        args.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[outa]",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                ffmpeg_argv_path(out_write),
            ]
        )
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-4000:]
            raise FFmpegCompileError(tail.strip() or "ffmpeg audio concat failed")
        if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 32:
            raise FFmpegCompileError("ffmpeg produced empty audio output")

        if st_root is not None:
            copy_short_to_destination(out_write, output_path)

        return {
            "output_path": str(output_path),
            "bytes": path_stat(output_path).st_size,
            "input_count": n,
            "mode": "audio_concat",
        }
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
