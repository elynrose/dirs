"""Concat multiple video files into one H.264 MP4 (video stream only)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ffmpeg_pipelines.errors import FFmpegCompileError, ffmpeg_cli_excerpt
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
    path_requires_nt_ffmpeg_staging,
    path_stat,
    replace_file_atomically,
    unlink_optional,
)

# Windows: many "-i" absolute paths + filter_complex can overflow argv; long paths also need smaller batches.
_MAX_FFMPEG_INPUTS_PER_INVOCATION = 12 if os.name == "nt" else 48


def _concat_batch_cap(paths: list[Path]) -> int:
    """Reduce inputs per ffmpeg run when each ``-i`` path is very long (Windows argv limits)."""
    if os.name != "nt" or not paths:
        return _MAX_FFMPEG_INPUTS_PER_INVOCATION
    max_arg = max(len(ffmpeg_argv_path(p)) for p in paths)
    # Leave headroom for ffmpeg_bin, filter_complex (~110 chars/input), and output path.
    budget = 6800
    per = max_arg + 130
    cap = max(2, min(_MAX_FFMPEG_INPUTS_PER_INVOCATION, budget // max(per, 1)))
    return cap


def _preflight_concat_inputs_have_video(paths: list[Path], ffprobe_bin: str | None) -> None:
    if not ffprobe_bin or not str(ffprobe_bin).strip() or not shutil.which(ffprobe_bin):
        return
    for idx, p in enumerate(paths):
        proc = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                ffmpeg_argv_path(p),
            ],
            capture_output=True,
            text=True,
            timeout=120.0,
        )
        line = (proc.stdout or "").strip().split("\n", 1)[0].strip()
        if proc.returncode != 0 or not line:
            hint = (proc.stderr or "").strip()[-1500:]
            raise FFmpegCompileError(
                f"concat input #{idx} has no decodable video stream: {p}\n{hint or 'ffprobe returned no codec'}"
            )


def _compile_video_concat_single_invocation(
    paths: list[Path],
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    crf: int,
    preset: str,
    ffmpeg_bin: str,
    ffprobe_bin: str | None,
    timeout_sec: float,
    run_ffprobe_preflight: bool,
) -> None:
    """One ffmpeg process: scale/pad each input, concat. Caller ensures len(paths) <= batch cap."""
    n = len(paths)
    st_root: Path | None = None
    try:
        if os.name == "nt" and concat_should_use_short_temp(paths, output):
            st_root = make_short_concat_staging_dir()
            # Always re-host inputs under %TEMP% when staging: triggers include argv pressure / input count,
            # not only per-path length (see nt_staging.concat_should_use_short_temp).
            paths_eff = stage_inputs_as_hardlink_or_copy(paths, st_root)
            part = st_root / "out.part"
        else:
            paths_eff = list(paths)
            part = output.with_name(output.name + ".part")

        if run_ffprobe_preflight:
            _preflight_concat_inputs_have_video(paths_eff, ffprobe_bin)

        unlink_optional(part)

        args: list[str] = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "warning",
            "-hwaccel",
            "none",
        ]
        for p in paths_eff:
            args.extend(["-i", ffmpeg_argv_path(p)])

        scale_pad = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"format=yuv420p,fps={fps},setsar=1,setpts=PTS-STARTPTS"
        )
        scaled = [f"[{i}:v]{scale_pad}[v{i}]" for i in range(n)]
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        concat = f"{concat_inputs}concat=n={n}:v=1:a=0[outv]"
        filter_complex = ";".join(scaled) + ";" + concat

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
            tail = ffmpeg_cli_excerpt(proc.stderr, proc.stdout)
            raise FFmpegCompileError(tail or "ffmpeg failed with no stderr")
        if not path_is_readable_file(part) or path_stat(part).st_size < 32:
            unlink_optional(part)
            raise FFmpegCompileError("ffmpeg produced empty or missing output")

        if st_root is not None:
            copy_short_to_destination(part, output)
            unlink_optional(part)
        else:
            replace_file_atomically(part, output)
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)


def _stream_copy_join(
    partials: list[Path],
    output: Path,
    *,
    ffmpeg_bin: str,
    timeout_sec: float,
) -> None:
    """
    Concatenate same-codec, same-resolution H.264 partials using the FFmpeg concat demuxer
    with ``-c copy``.  No decode/encode — zero generation loss.

    All inputs must have identical codec parameters (same width, height, fps, pixel format).
    This is guaranteed when all partials were produced by ``_compile_video_concat_single_invocation``
    or ``compile_image_slideshow`` with the same width/height/fps/crf/preset arguments.

    The concat list file is written inside the partials' parent directory (already a short
    temp path), so only the *output* path needs NT-staging consideration.

    Output is **video only** (``-map 0:v:0`` + ``-an``): stock clips (e.g. Pexels) must never
    contribute an audio stream here, even if a partial mistakenly contained one.
    """
    if not partials:
        raise FFmpegCompileError("_stream_copy_join: no input partials")

    list_file = partials[0].parent / f"scj_{uuid.uuid4().hex[:10]}.txt"
    st_root: Path | None = None
    try:
        with list_file.open("w", encoding="utf-8") as fh:
            for p in partials:
                # FFmpeg concat demuxer uses C-style escaping; backslashes must be doubled.
                escaped = ffmpeg_argv_path(p).replace("\\", "\\\\").replace("'", "\\'")
                fh.write(f"file '{escaped}'\n")

        out_write = output
        if os.name == "nt" and path_requires_nt_ffmpeg_staging(output):
            st_root = make_short_concat_staging_dir()
            out_write = st_root / "joined.mp4"
        else:
            mkdir_parent(output)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            ffmpeg_argv_path(list_file),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-an",
            "-movflags",
            "+faststart",
            ffmpeg_argv_path(out_write),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            tail = ffmpeg_cli_excerpt(proc.stderr, proc.stdout)
            raise FFmpegCompileError(tail or "stream-copy join failed")
        if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 32:
            raise FFmpegCompileError("stream-copy join produced empty output")

        if st_root is not None:
            copy_short_to_destination(out_write, output)
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
        try:
            list_file.unlink(missing_ok=True)
        except OSError:
            pass


def compile_video_concat(
    paths: list[Path],
    output: Path,
    *,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    crf: int = 23,
    preset: str = "veryfast",
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str | None = "ffprobe",
    timeout_sec: float = 900.0,
    _original_input_count: int | None = None,
    _intermediate: bool = False,
) -> dict[str, Any]:
    """
    Concatenate videos with per-segment scale/pad/fps normalization.

    On Windows, long timelines are merged in batches so the ffmpeg command line stays under OS limits.
    """
    if not paths:
        raise FFmpegCompileError("no video inputs")

    original_n = _original_input_count if _original_input_count is not None else len(paths)

    for p in paths:
        if not path_is_readable_file(p):
            raise FFmpegCompileError(f"input not found: {p}")

    output = output.resolve()
    mkdir_parent(output)

    cap = _concat_batch_cap(paths)
    if len(paths) <= cap:
        _compile_video_concat_single_invocation(
            paths,
            output,
            width=width,
            height=height,
            fps=fps,
            crf=crf,
            preset=preset,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
            run_ffprobe_preflight=not _intermediate,
        )
        return {
            "output_path": str(output),
            "bytes": path_stat(output).st_size,
            "input_count": original_n,
            "mode": "video_concat",
            "chunked_concat": original_n > len(paths),
        }

    if os.name == "nt":
        work = Path(tempfile.mkdtemp(prefix="vcc_", dir=tempfile.gettempdir()))
    else:
        work = output.parent / f".vconcat_{uuid.uuid4().hex}"
        work.mkdir(parents=True, exist_ok=True)
    try:
        partials: list[Path] = []
        for i in range(0, len(paths), cap):
            chunk = paths[i : i + cap]
            part_path = work / f"{uuid.uuid4().hex}.mp4"
            compile_video_concat(
                chunk,
                part_path,
                width=width,
                height=height,
                fps=fps,
                crf=crf,
                preset=preset,
                ffmpeg_bin=ffmpeg_bin,
                ffprobe_bin=ffprobe_bin,
                timeout_sec=timeout_sec,
                _original_input_count=original_n,
                _intermediate=True,
            )
            partials.append(part_path)
        # Merge chunk files with stream-copy (no re-encode) — all partials are already
        # the correct codec / dimensions / fps so a second encode is unnecessary.
        _stream_copy_join(partials, output, ffmpeg_bin=ffmpeg_bin, timeout_sec=timeout_sec)
        return {
            "output_path": str(output),
            "bytes": path_stat(output).st_size,
            "input_count": original_n,
            "mode": "video_concat",
            "chunked_concat": True,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
