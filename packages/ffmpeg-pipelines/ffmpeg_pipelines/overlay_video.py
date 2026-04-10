"""Burn simple title / lower-third / map-placeholder overlays on a video (fine cut, local FFmpeg)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.filter_probe import ffmpeg_filter_available
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


def _sanitize_drawtext(s: str, max_len: int = 100) -> str:
    """Keep drawtext-safe ASCII-ish text; escape single quotes for ffmpeg."""
    raw = (s or "").strip()[:max_len]
    out = []
    for c in raw:
        if c == "\\":
            out.append("\\\\")
        elif c == "'":
            out.append("\\'")
        elif c == ":":
            out.append("\\:")
        elif c == "%":
            out.append("\\%")
        elif ord(c) >= 32 and ord(c) < 127 and c not in "\n\r\t":
            out.append(c)
        elif c in " .,!?-+":
            out.append(c)
    t = "".join(out).strip()
    return t or "—"


def build_overlay_filter_chain(overlays: list[dict[str, Any]]) -> tuple[str, str] | None:
    """
    Returns ``(filter_complex_prefix, last_video_label)`` for chaining ``format=yuv420p[outv]``.
    """
    if not overlays:
        return None
    steps: list[str] = []
    prev = "0:v"
    last_label = prev
    step_i = 0
    for ov in overlays:
        if not isinstance(ov, dict):
            continue
        t0 = float(ov.get("start_sec") or 0)
        t1 = float(ov.get("end_sec") or 0)
        if t1 <= t0:
            continue
        typ = str(ov.get("type") or "").lower()
        out_lab = f"ov{step_i}"
        step_i += 1
        # Commas inside filter args must be escaped for filtergraph parsing.
        en = f"between(t\\,{t0}\\,{t1})"
        if typ == "title_card":
            text = _sanitize_drawtext(str(ov.get("text") or "Title"))
            filt = (
                f"drawtext=text='{text}':fontsize=52:fontcolor=white:borderw=2:bordercolor=black@0.6:"
                f"x=(w-text_w)/2:y=(h-text_h)/3:enable='{en}'"
            )
        elif typ == "lower_third":
            line1 = _sanitize_drawtext(str(ov.get("text") or "Name"))
            line2 = _sanitize_drawtext(str(ov.get("subtext") or ""), 80)
            if line2:
                filt = (
                    f"drawtext=text='{line1}':fontsize=36:fontcolor=white:borderw=2:bordercolor=black@0.5:"
                    f"x=48:y=h-120:enable='{en}',"
                    f"drawtext=text='{line2}':fontsize=26:fontcolor=white@0.95:"
                    f"x=48:y=h-78:enable='{en}'"
                )
            else:
                filt = (
                    f"drawtext=text='{line1}':fontsize=36:fontcolor=white:borderw=2:bordercolor=black@0.5:"
                    f"x=48:y=h-100:enable='{en}'"
                )
        elif typ == "map_placeholder":
            label = _sanitize_drawtext(str(ov.get("label") or "Map"), 60)
            filt = (
                f"drawbox=x=48:y=48:w=280:h=180:color=black@0.45:t=fill:enable='{en}',"
                f"drawtext=text='{label}':fontsize=28:fontcolor=white:borderw=1:bordercolor=white@0.4:"
                f"x=64:y=120:enable='{en}'"
            )
        else:
            continue
        steps.append(f"[{prev}]{filt}[{out_lab}]")
        prev = out_lab
        last_label = out_lab
    if not steps:
        return None
    return ";".join(steps), last_label


def burn_overlays_on_video(
    input_video: Path,
    output_video: Path,
    overlays: list[dict[str, Any]],
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 900.0,
) -> dict[str, Any]:
    """Re-encode video with overlay filters. **No audio** — clip sound is dropped; final mux adds VO + music."""
    input_video = input_video.resolve()
    output_video = output_video.resolve()
    if not path_is_readable_file(input_video):
        raise FFmpegCompileError(f"input video not found: {input_video}")
    mkdir_parent(output_video)

    built = build_overlay_filter_chain(overlays)
    if not built:
        part = output_video.with_name(output_video.name + ".part")
        unlink_optional(part)
        # Stream-copy video only so source clip audio is never carried into fine_cut.
        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                ffmpeg_argv_path(input_video),
                "-map",
                "0:v:0",
                "-c:v",
                "copy",
                "-an",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                ffmpeg_argv_path(part),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if proc.returncode != 0:
            unlink_optional(part)
            tail = (proc.stderr or proc.stdout or "")[-5000:]
            raise FFmpegCompileError(tail.strip() or "fine_cut copy pass failed")
        if not path_is_readable_file(part) or path_stat(part).st_size < 32:
            unlink_optional(part)
            raise FFmpegCompileError("fine_cut copy pass empty")
        replace_file_atomically(part, output_video)
        return {"mode": "copy_no_overlays", "bytes": path_stat(output_video).st_size}

    chain, out_label = built
    full = f"{chain};[{out_label}]format=yuv420p[outv]"

    if "drawtext" in chain and not ffmpeg_filter_available(ffmpeg_bin, "drawtext"):
        raise FFmpegCompileError(
            "FFmpeg build has no drawtext filter (needs freetype). "
            "Use a full FFmpeg package or run fine_cut on a build with libfreetype."
        )

    def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)

    st_root: Path | None = None
    video_in = input_video
    part = output_video.with_name(output_video.name + ".part")
    try:
        if os.name == "nt" and concat_should_use_short_temp([input_video], output_video):
            st_root = make_short_concat_staging_dir()
            video_in = stage_inputs_as_hardlink_or_copy([input_video], st_root)[0]
            part = st_root / "out.part"

        unlink_optional(part)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            ffmpeg_argv_path(video_in),
            "-filter_complex",
            full,
            "-map",
            "[outv]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-an",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            ffmpeg_argv_path(part),
        ]
        proc = _run(cmd)
        if proc.returncode != 0:
            unlink_optional(part)
            tail = (proc.stderr or proc.stdout or "")[-5000:]
            raise FFmpegCompileError(tail.strip() or "overlay encode failed")
        if not path_is_readable_file(part) or path_stat(part).st_size < 32:
            unlink_optional(part)
            raise FFmpegCompileError("overlay output empty")
        if st_root is not None:
            copy_short_to_destination(part, output_video)
        else:
            replace_file_atomically(part, output_video)
        return {
            "output_path": str(output_video),
            "bytes": path_stat(output_video).st_size,
            "overlay_count": len(overlays),
            "mode": "fine_cut_overlays",
        }
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
