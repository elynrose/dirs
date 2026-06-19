"""Encode a still image to H.264 MP4 (local storage / Phase 3 video)."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from ffmpeg_pipelines.encode import VideoEncodeConfig, append_video_encode_args, effective_encode_config
from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.ken_burns import build_crop_pan_vf, build_slow_zoom_vf
from ffmpeg_pipelines.nt_staging import (
    concat_should_use_short_temp,
    copy_short_to_destination,
    make_short_concat_staging_dir,
    stage_inputs_as_hardlink_or_copy,
)
from ffmpeg_pipelines.paths import ffmpeg_argv_path, mkdir_parent, path_is_readable_file, path_stat
MotionMode = Literal["none", "pan", "zoom"]


def encode_image_to_mp4(
    image_path: Path,
    output_path: Path,
    *,
    duration_sec: float = 4.0,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    crf: int = 23,
    preset: str = "veryfast",
    encode_config: VideoEncodeConfig | None = None,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 180.0,
    slow_zoom: bool = False,
    motion: MotionMode | None = None,
    ken_burns_direction: Literal["in", "out"] = "in",
    ken_burns_easing: Literal["linear", "smooth"] = "smooth",
) -> dict[str, Any]:
    """Loop input still for ``duration_sec``, scale/pad (or Ken Burns zoom/pan) to frame size, H.264 + AAC silence."""
    image_path = image_path.resolve()
    output_path = output_path.resolve()
    if not path_is_readable_file(image_path):
        raise FFmpegCompileError(f"image not found: {image_path}")
    dur = max(0.5, min(float(duration_sec), 7200.0))

    eff_motion: MotionMode
    if motion is not None:
        eff_motion = motion
    elif slow_zoom:
        eff_motion = "zoom"
    else:
        eff_motion = "none"

    st_root = None
    img_in = image_path
    out_write = output_path
    try:
        if os.name == "nt" and concat_should_use_short_temp([image_path], output_path):
            st_root = make_short_concat_staging_dir()
            img_in = stage_inputs_as_hardlink_or_copy([image_path], st_root)[0]
            out_write = st_root / "out.mp4"
        else:
            mkdir_parent(output_path)

        if eff_motion == "zoom":
            vf = build_slow_zoom_vf(
                width=width,
                height=height,
                fps=fps,
                duration_sec=dur,
                direction=ken_burns_direction,
                easing=ken_burns_easing,
            )
        elif eff_motion == "pan":
            pan_dir = random.choice(("left", "right"))
            vf = build_crop_pan_vf(
                width=width,
                height=height,
                fps=fps,
                duration_sec=dur,
                direction=pan_dir,
            )
        else:
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"fps={fps},format=yuv420p"
            )
        enc = effective_encode_config(encode_config, crf=crf, preset=preset)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-loop",
            "1",
            "-i",
            ffmpeg_argv_path(img_in),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{dur:.3f}",
            "-vf",
            vf,
        ]
        append_video_encode_args(cmd, enc)
        cmd.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "-shortest",
                ffmpeg_argv_path(out_write),
            ]
        )
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-4000:]
            raise FFmpegCompileError(tail.strip() or "still_to_video failed")
        if st_root is not None:
            copy_short_to_destination(out_write, output_path)
        if not path_is_readable_file(output_path) or path_stat(output_path).st_size < 32:
            raise FFmpegCompileError("encoder produced empty output")
        return {
            "output_path": str(output_path),
            "bytes": path_stat(output_path).st_size,
            "duration_sec": dur,
            "mode": "still_to_video_local",
            "motion": eff_motion,
            "slow_zoom": eff_motion == "zoom",
            "ken_burns_direction": ken_burns_direction if eff_motion == "zoom" else None,
            "ken_burns_easing": ken_burns_easing if eff_motion == "zoom" else None,
            **enc.as_compile_meta(),
        }
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
