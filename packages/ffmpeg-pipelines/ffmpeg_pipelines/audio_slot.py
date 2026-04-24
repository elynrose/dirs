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
from ffmpeg_pipelines.probe import ffprobe_duration_seconds


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


def normalize_audio_segment_to_duration(
    input_path: Path,
    output_path: Path,
    duration_sec: float,
    *,
    start_offset_sec: float = 0.0,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 180.0,
) -> None:
    """Extract ``duration_sec`` of audio starting at ``start_offset_sec``, re-encoded to AAC (stereo 48 kHz).

    Trims from the source then pads with silence to exactly ``duration_sec``. If ``start_offset_sec`` is
    past the end of the file, output is silence of ``duration_sec``.
    """
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if not path_is_readable_file(input_path):
        raise FFmpegCompileError(f"audio input not found: {input_path}")

    d = max(0.05, min(float(duration_sec), 7200.0))
    dstr = f"{d:.3f}"
    t0 = max(0.0, float(start_offset_sec))
    try:
        src_len = float(
            ffprobe_duration_seconds(input_path, ffprobe_bin=ffprobe_bin, timeout_sec=min(120.0, timeout_sec))
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError, TypeError):
        src_len = 0.0
    if src_len <= 0 or t0 >= src_len - 0.001:
        # No samples left at this offset — output silence of exact duration (same encoder as other stem segments).
        st_root2: Path | None = None
        out_write2 = output_path
        try:
            if audio_should_use_short_temp([output_path]):
                st_root2 = make_short_concat_staging_dir()
                out_write2 = st_root2 / "sil.m4a"
            else:
                mkdir_parent(output_path)
            cmd_sil = [
                ffmpeg_bin,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t",
                dstr,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                ffmpeg_argv_path(out_write2),
            ]
            proc_sil = subprocess.run(cmd_sil, capture_output=True, text=True, timeout=timeout_sec)
            if proc_sil.returncode != 0:
                tail = (proc_sil.stderr or proc_sil.stdout or "")[-4000:]
                raise FFmpegCompileError(tail.strip() or "silence segment failed")
            if not path_is_readable_file(out_write2) or path_stat(out_write2).st_size < 32:
                raise FFmpegCompileError("silence segment produced empty output")
            if st_root2 is not None:
                copy_short_to_destination(out_write2, output_path)
        finally:
            if st_root2 is not None:
                shutil.rmtree(st_root2, ignore_errors=True)
        return

    t1 = min(t0 + d, src_len)
    t0s = f"{t0:.3f}"
    t1s = f"{t1:.3f}"
    af = (
        "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000,"
        f"atrim={t0s}:{t1s},asetpts=PTS-STARTPTS,apad=whole_dur={dstr}"
    )

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
            raise FFmpegCompileError(tail.strip() or "normalize_audio_segment_to_duration failed")
        if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 32:
            raise FFmpegCompileError("normalize_audio_segment_to_duration produced empty output")

        if st_root is not None:
            copy_short_to_destination(out_write, output_path)
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
