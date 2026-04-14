"""Burn WebVTT subtitles into an MP4 (post mux) using FFmpeg ``subtitles`` filter."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def burn_webvtt_onto_mp4(
    *,
    video_in: Path,
    vtt_path: Path,
    video_out: Path,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 3600.0,
) -> None:
    """Re-encode video with burned-in subtitles; copies audio stream unchanged."""
    video_in = video_in.resolve()
    vtt_path = vtt_path.resolve()
    video_out = video_out.resolve()
    if not video_in.is_file():
        raise FileNotFoundError(str(video_in))
    if not vtt_path.is_file():
        raise FileNotFoundError(str(vtt_path))
    work = video_out.parent
    work.mkdir(parents=True, exist_ok=True)
    local_vtt = work / "_burn_subtitles.vtt"
    shutil.copyfile(vtt_path, local_vtt)
    tmp_out = work / "_burn_video_out.mp4"
    try:
        # Run from ``work`` so the filter path stays short and portable.
        vf = f"subtitles={local_vtt.name}:charenc=UTF-8"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            video_in.name,
            "-vf",
            vf,
            "-c:a",
            "copy",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            tmp_out.name,
        ]
        subprocess.run(
            cmd,
            cwd=str(work),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if not tmp_out.is_file():
            raise RuntimeError("ffmpeg produced no output file")
        if video_out.is_file():
            video_out.unlink()
        tmp_out.replace(video_out)
    finally:
        if local_vtt.is_file():
            try:
                local_vtt.unlink()
            except OSError:
                pass
        if tmp_out.is_file() and tmp_out != video_out:
            try:
                tmp_out.unlink()
            except OSError:
                pass
