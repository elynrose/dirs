"""Concat still images into one H.264 MP4 with optional motion + crossfades."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.ken_burns import build_crop_pan_vf, build_slow_zoom_vf
from ffmpeg_pipelines.nt_staging import (
    concat_should_use_short_temp,
    copy_short_to_destination,
    make_short_concat_staging_dir,
    stage_inputs_as_hardlink_or_copy,
)
from ffmpeg_pipelines.paths import (
    ffmpeg_argv_path,
    mkdir_parent,
    path_is_readable_file,
    path_stat,
    replace_file_atomically,
    unlink_optional,
)

MotionMode = Literal["none", "pan", "zoom"]

# Windows CreateProcessW passes one command-line string (typically ≤32767 chars). The slideshow argv includes
# many ``-loop/-t/-i`` triples plus a large ``-filter_complex`` (per-slide zoompan/pan chains and xfade links).
# ``list2cmdline`` quoting can expand ``\\?\``-style paths; pan+motion uses long easing expressions; long slide
# durations lengthen ``zoompan`` ``d=`` / ``on/denom`` substrings. Batches of 20 (then 10) still hit
# ``[WinError 206]`` in production — stay conservative on NT.
_SLIDESHOW_BATCH_CAP_NT = 6
_SLIDESHOW_BATCH_CAP_OTHER = 100


def _slideshow_batch_cap() -> int:
    return _SLIDESHOW_BATCH_CAP_NT if os.name == "nt" else _SLIDESHOW_BATCH_CAP_OTHER


def compile_image_slideshow(
    slides: list[tuple[Path, float]],
    output: Path,
    *,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    crf: int = 23,
    preset: str = "veryfast",
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 900.0,
    slow_zoom: bool = False,
    motion: MotionMode | None = None,
    crossfade_sec: float = 0.75,
) -> dict[str, Any]:
    """
    Each slide is shown for ``duration`` seconds (looping the still).

    ``motion`` controls per-slide visual effect:

    * ``"pan"``  -- crop-based diagonal drift (fast, ~10-20x quicker than zoom).
    * ``"zoom"`` -- zoompan Ken Burns (slow, high quality).
    * ``"none"`` -- static scale+pad.

    ``crossfade_sec`` adds a dissolve transition between consecutive slides
    (0 = hard cut).  Clamped to at most half the shortest slide duration.

    When ``motion`` is *None* the legacy ``slow_zoom`` flag decides:
    ``True`` -> ``"pan"``, ``False`` -> ``"none"`` (default: static assembly).
    """
    if motion is None:
        motion = "pan" if slow_zoom else "none"

    if not slides:
        raise FFmpegCompileError("no slides")
    for p, dur in slides:
        if not path_is_readable_file(p):
            raise FFmpegCompileError(f"input not found: {p}")
        if dur <= 0:
            raise FFmpegCompileError(f"duration must be > 0, got {dur}")

    # On Windows, zoompan filter chains make the filter_complex grow ~320 chars per slide.
    # 83 slides → 38 k-char command line → CreateProcessW WinError 206. Split into safe batches.
    cap = _slideshow_batch_cap()
    if len(slides) > cap:
        from ffmpeg_pipelines.video_chain import _stream_copy_join

        work = Path(tempfile.mkdtemp(prefix=f"ssbatch_{uuid.uuid4().hex[:8]}_", dir=tempfile.gettempdir()))
        try:
            chunk_paths: list[Path] = []
            for i in range(0, len(slides), cap):
                chunk = slides[i : i + cap]
                chunk_out = work / f"chunk_{i:04d}.mp4"
                compile_image_slideshow(
                    chunk,
                    chunk_out,
                    width=width,
                    height=height,
                    fps=fps,
                    crf=crf,
                    preset=preset,
                    ffmpeg_bin=ffmpeg_bin,
                    timeout_sec=timeout_sec,
                    slow_zoom=slow_zoom,
                    motion=motion,
                    crossfade_sec=crossfade_sec,
                )
                chunk_paths.append(chunk_out)
            # Chunks are already the correct codec/dimensions — stream-copy join avoids
            # a second lossy encode (the batch cap exists only to keep argv under OS limits,
            # not because re-encoding is needed).
            _stream_copy_join(chunk_paths, output, ffmpeg_bin=ffmpeg_bin, timeout_sec=timeout_sec)
            return {
                "output_path": str(output),
                "bytes": path_stat(output).st_size,
                "mode": "image_slideshow",
                "slide_count": len(slides),
                "motion": motion,
                "slow_zoom": motion in ("pan", "zoom"),
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)

    output = output.resolve(strict=False)
    n = len(slides)
    input_paths = [p.resolve(strict=False) for p, _ in slides]

    st_root: Path | None = None
    try:
        if os.name == "nt" and concat_should_use_short_temp(input_paths, output):
            st_root = make_short_concat_staging_dir()
            staged = stage_inputs_as_hardlink_or_copy(input_paths, st_root)
            slides_run: list[tuple[Path, float]] = [
                (staged[i], slides[i][1]) for i in range(n)
            ]
            part = st_root / "out.part"
        else:
            slides_run = [(input_paths[i], slides[i][1]) for i in range(n)]
            mkdir_parent(output)
            part = output.with_name(output.name + ".part")

        unlink_optional(part)

        xf = max(0.0, float(crossfade_sec))
        if n >= 2 and xf > 0:
            min_dur = min(d for _, d in slides_run)
            xf = min(xf, min_dur * 0.45)
        else:
            xf = 0.0

        args: list[str] = [ffmpeg_bin, "-y"]
        for path, dur in slides_run:
            args.extend(
                ["-loop", "1", "-t", f"{dur:.6f}", "-i", ffmpeg_argv_path(path.resolve(strict=False))]
            )

        scale_pad = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},format=yuv420p,setpts=PTS-STARTPTS"
        )
        chains: list[str] = []
        for i in range(n):
            _path, dur_i = slides_run[i]
            if motion == "zoom":
                direction = "out" if (i % 2 == 1) else "in"
                zvf = build_slow_zoom_vf(
                    width=width,
                    height=height,
                    fps=fps,
                    duration_sec=float(dur_i),
                    direction=direction,
                )
                chain = f"{zvf},setpts=PTS-STARTPTS"
            elif motion == "pan":
                pan_dir = random.choice(("left", "right"))
                pvf = build_crop_pan_vf(
                    width=width,
                    height=height,
                    fps=fps,
                    duration_sec=float(dur_i),
                    direction=pan_dir,
                )
                chain = f"{pvf},setpts=PTS-STARTPTS"
            else:
                chain = scale_pad
            chains.append(f"[{i}:v]{chain}[v{i}]")

        if xf > 0 and n >= 2:
            xfade_parts = _build_xfade_chain(
                n=n,
                durations=[d for _, d in slides_run],
                xfade_sec=xf,
            )
            filter_complex = ";".join(chains) + ";" + ";".join(xfade_parts)
        else:
            concat_inputs = "".join(f"[v{i}]" for i in range(n))
            concat = f"{concat_inputs}concat=n={n}:v=1:a=0[outv]"
            filter_complex = ";".join(chains) + ";" + concat

        args.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[outv]",
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-movflags",
                "+faststart",
                "-an",
                "-f",
                "mp4",
                ffmpeg_argv_path(part),
            ]
        )

        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            unlink_optional(part)
            tail = (proc.stderr or proc.stdout or "")[-4000:]
            raise FFmpegCompileError(tail.strip() or "ffmpeg failed with no stderr")
        if not path_is_readable_file(part) or path_stat(part).st_size < 32:
            unlink_optional(part)
            raise FFmpegCompileError("ffmpeg produced empty or missing output")
        if st_root is not None:
            copy_short_to_destination(part, output)
            unlink_optional(part)
        else:
            replace_file_atomically(part, output)

        return {
            "output_path": str(output),
            "bytes": path_stat(output).st_size,
            "slide_count": n,
            "mode": "image_slideshow",
            "motion": motion,
            "crossfade_sec": xf,
            "slow_zoom": motion in ("pan", "zoom"),
        }
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)


def _build_xfade_chain(
    *,
    n: int,
    durations: list[float],
    xfade_sec: float,
) -> list[str]:
    """
    Build chained ``xfade`` filter expressions for *n* pre-scaled segments
    labelled ``[v0]`` .. ``[v{n-1}]``, producing ``[outv]``.

    Each transition is a ``fade`` dissolve of ``xfade_sec`` seconds.
    The offset for transition *k* is the cumulative duration of segments
    0..k minus *k* overlaps (each overlap shortens the timeline by xfade_sec).
    """
    parts: list[str] = []
    cumulative = durations[0]
    for k in range(n - 1):
        offset = cumulative - xfade_sec
        left = f"[v{k}]" if k == 0 else f"[x{k}]"
        right = f"[v{k + 1}]"
        out_label = "[outv]" if k == n - 2 else f"[x{k + 1}]"
        parts.append(
            f"{left}{right}xfade=transition=fade:duration={xfade_sec:.4f}"
            f":offset={offset:.4f}{out_label}"
        )
        if k + 1 < len(durations):
            cumulative += durations[k + 1] - xfade_sec
    return parts
