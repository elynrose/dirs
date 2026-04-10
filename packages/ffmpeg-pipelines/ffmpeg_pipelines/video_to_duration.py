"""Re-encode a video clip to an exact duration (trim or loop), video-only."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.nt_staging import (
    concat_should_use_short_temp,
    copy_short_to_destination,
    make_short_concat_staging_dir,
    stage_inputs_as_hardlink_or_copy,
)
from ffmpeg_pipelines.paths import ffmpeg_argv_path, mkdir_parent, path_is_readable_file, path_stat
from ffmpeg_pipelines.probe import ffprobe_duration_seconds


def encode_video_to_target_duration_mp4(
    video_path: Path,
    output_path: Path,
    *,
    target_sec: float,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    crf: int = 23,
    preset: str = "veryfast",
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 900.0,
) -> None:
    """
    Output length exactly ``target_sec`` (trim if source is longer, loop if shorter). No audio.
    """
    video_path = video_path.resolve()
    output_path = output_path.resolve()
    if not path_is_readable_file(video_path):
        raise FFmpegCompileError(f"video not found: {video_path}")
    target = max(0.5, min(float(target_sec), 7200.0))
    tstr = f"{target:.3f}"

    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"format=yuv420p,fps={fps},setsar=1,setpts=PTS-STARTPTS"
    )

    st_root: Path | None = None
    vin = video_path
    out_write = output_path
    try:
        if os.name == "nt" and concat_should_use_short_temp([video_path], output_path):
            st_root = make_short_concat_staging_dir()
            vin = stage_inputs_as_hardlink_or_copy([video_path], st_root)[0]
            out_write = st_root / "vid_dur.mp4"
        else:
            mkdir_parent(output_path)

        # Probe duration AFTER staging so ffprobe always receives a short path on Windows.
        # (video_path may be long enough to trigger staging but not long enough for the pre-staging
        # check to fail — probing the staged copy is always safe and avoids WinError 206.)
        native = float(
            ffprobe_duration_seconds(vin, ffprobe_bin=ffprobe_bin, timeout_sec=min(120.0, timeout_sec))
        )
        if native <= 0:
            raise FFmpegCompileError(f"could not probe duration: {video_path}")

        if target <= native + 0.08:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                ffmpeg_argv_path(vin),
                "-t",
                tstr,
                "-vf",
                scale_pad,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-movflags",
                "+faststart",
                ffmpeg_argv_path(out_write),
            ]
        else:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                ffmpeg_argv_path(vin),
                "-t",
                tstr,
                "-vf",
                scale_pad,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-movflags",
                "+faststart",
                ffmpeg_argv_path(out_write),
            ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-5000:]
            raise FFmpegCompileError(tail.strip() or "encode_video_to_target_duration failed")
        if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 32:
            raise FFmpegCompileError("encode_video_to_target_duration produced empty output")
        if st_root is not None:
            copy_short_to_destination(out_write, output_path)
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
