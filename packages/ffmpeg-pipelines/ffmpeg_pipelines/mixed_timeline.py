"""Rough-cut compile: mix image and video clip assets into one H.264 MP4 (video only).

Consecutive stills are batched via ``compile_image_slideshow`` (optional Ken Burns / pan + crossfade).
Videos are normalized and concatenated; final join is stream-copy when all partials share encode settings.

Per-clip video normalization uses video-only encodes (``-an`` / concat ``a=0``); the final
``_stream_copy_join`` pass maps only the video stream so stock sources (e.g. Pexels) cannot
attach clip audio to ``rough_cut.mp4``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal, Union

from ffmpeg_pipelines.black_title_card import encode_black_title_card_mp4
from ffmpeg_pipelines.encode import VideoEncodeConfig, effective_encode_config
from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.paths import path_is_readable_file, path_stat
from ffmpeg_pipelines.probe import ffprobe_duration_seconds
from ffmpeg_pipelines.slideshow import MotionMode, compile_image_slideshow
from ffmpeg_pipelines.still_to_video import encode_image_to_mp4
from ffmpeg_pipelines.video_chain import _stream_copy_join, compile_video_concat
from ffmpeg_pipelines.video_to_duration import encode_video_to_target_duration_mp4

SegKind = Literal["image", "video", "chapter_title"]

VisualSegment = Union[
    tuple[Literal["image"], Path, float],
    tuple[Literal["image"], Path, float, MotionMode],
    tuple[Literal["video"], Path, None],
    tuple[Literal["video"], Path, float],
    tuple[Literal["chapter_title"], str, float],
]


def _image_segment_motion(seg: VisualSegment) -> MotionMode:
    if seg[0] != "image":
        return "none"
    if len(seg) >= 4:
        m = seg[3]
        if m in ("none", "pan", "zoom"):
            return m
    return "none"


def compile_mixed_visual_timeline(
    segments: list[VisualSegment],
    output: Path,
    *,
    width: int = 1280,
    height: int = 720,
    crf: int = 23,
    preset: str = "veryfast",
    encode_config: VideoEncodeConfig | None = None,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str | None = "ffprobe",
    timeout_sec: float = 900.0,
    image_batch_crossfade_sec: float = 0.0,
    export_ffmpeg_registry: object | None = None,
) -> dict[str, Any]:
    """
    Each segment is either:

    - ``("image", path, duration_sec)`` or ``("image", path, duration_sec, motion)`` — still clip.
    - ``("video", path, None)`` — full clip (native length after concat normalize).
    - ``("video", path, duration_sec)`` — trim or loop to exactly ``duration_sec``.
    - ``("chapter_title", text, duration_sec)`` — black full-frame card with centered ``text``.

    Consecutive image segments are batched into one slideshow encode per run when possible.
    """
    if not segments:
        raise FFmpegCompileError("no segments")
    enc = effective_encode_config(encode_config, crf=crf, preset=preset)
    if os.name == "nt":
        work_root = Path(tempfile.mkdtemp(prefix="mixtl_", dir=tempfile.gettempdir()))
    else:
        work_root = output.resolve().parent / f".mixed_timeline_{uuid.uuid4().hex}"
        work_root.mkdir(parents=True, exist_ok=True)
    vpaths: list[Path] = []
    temp_encoded: list[Path] = []
    image_run: list[tuple[Path, float, MotionMode]] = []
    xf = max(0.0, float(image_batch_crossfade_sec))

    def flush_image_run() -> None:
        if not image_run:
            return
        motions = [m for _, _, m in image_run]
        if len(image_run) == 1:
            path, dur, motion = image_run[0]
            tmp_out = work_root / f"seg_{len(vpaths)}.mp4"
            encode_image_to_mp4(
                path,
                tmp_out,
                duration_sec=float(dur),
                width=width,
                height=height,
                crf=crf,
                preset=preset,
                encode_config=enc,
                motion=motion,
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=min(timeout_sec, 7200.0),
            )
            temp_encoded.append(tmp_out)
            vpaths.append(tmp_out)
        else:
            tmp_out = work_root / f"imgbatch_{len(vpaths)}_{uuid.uuid4().hex[:8]}.mp4"
            shared_motion: MotionMode | None = motions[0] if all(m == motions[0] for m in motions) else None
            compile_image_slideshow(
                [(p, float(d)) for p, d, _m in image_run],
                tmp_out,
                width=width,
                height=height,
                fps=30,
                crf=crf,
                preset=preset,
                encode_config=enc,
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=timeout_sec,
                motion=shared_motion if shared_motion is not None else "none",
                slide_motions=motions if shared_motion is None else None,
                crossfade_sec=xf,
                export_ffmpeg_registry=export_ffmpeg_registry,
            )
            temp_encoded.append(tmp_out)
            vpaths.append(tmp_out)
        image_run.clear()

    try:
        for seg in segments:
            kind = seg[0]
            if kind == "chapter_title":
                flush_image_run()
                _text, card_dur = seg[1], float(seg[2])
                if card_dur <= 0:
                    raise FFmpegCompileError("chapter_title requires duration_sec > 0")
                tmp_out = work_root / f"seg_{len(vpaths)}.mp4"
                encode_black_title_card_mp4(
                    tmp_out,
                    title=str(_text),
                    duration_sec=card_dur,
                    width=width,
                    height=height,
                    crf=crf,
                    preset=preset,
                    encode_config=enc,
                    ffmpeg_bin=ffmpeg_bin,
                    timeout_sec=min(timeout_sec, 300.0),
                )
                temp_encoded.append(tmp_out)
                vpaths.append(tmp_out)
                continue
            path = seg[1].resolve()
            dur = seg[2]
            if not path_is_readable_file(path):
                raise FFmpegCompileError(f"segment input not found: {path}")
            if kind == "image":
                if dur is None or float(dur) <= 0:
                    raise FFmpegCompileError("image segment requires duration_sec > 0")
                image_run.append((path, float(dur), _image_segment_motion(seg)))
            elif kind == "video":
                flush_image_run()
                dur_v = dur
                ffp = ffprobe_bin or "ffprobe"
                if dur_v is not None and float(dur_v) > 0:
                    try:
                        native = float(
                            ffprobe_duration_seconds(
                                path, ffprobe_bin=ffp, timeout_sec=min(120.0, float(timeout_sec))
                            )
                        )
                    except (FileNotFoundError, OSError, RuntimeError, ValueError, TypeError):
                        native = 0.0
                    if native > 0 and abs(float(dur_v) - native) <= 0.12:
                        tmp_out = work_root / f"vid_{len(vpaths)}_{uuid.uuid4().hex[:8]}.mp4"
                        compile_video_concat(
                            [path],
                            tmp_out,
                            width=width,
                            height=height,
                            fps=30,
                            crf=crf,
                            preset=preset,
                            encode_config=enc,
                            ffmpeg_bin=ffmpeg_bin,
                            ffprobe_bin=None,
                            timeout_sec=float(timeout_sec),
                        )
                        temp_encoded.append(tmp_out)
                        vpaths.append(tmp_out)
                    else:
                        tmp_out = work_root / f"vid_{len(vpaths)}_{uuid.uuid4().hex[:8]}.mp4"
                        encode_video_to_target_duration_mp4(
                            path,
                            tmp_out,
                            target_sec=float(dur_v),
                            width=width,
                            height=height,
                            fps=30,
                            crf=crf,
                            preset=preset,
                            encode_config=enc,
                            ffmpeg_bin=ffmpeg_bin,
                            ffprobe_bin=ffp,
                            timeout_sec=float(timeout_sec),
                        )
                        temp_encoded.append(tmp_out)
                        vpaths.append(tmp_out)
                else:
                    tmp_out = work_root / f"vid_{len(vpaths)}_{uuid.uuid4().hex[:8]}.mp4"
                    compile_video_concat(
                        [path],
                        tmp_out,
                        width=width,
                        height=height,
                        fps=30,
                        crf=crf,
                        preset=preset,
                        encode_config=enc,
                        ffmpeg_bin=ffmpeg_bin,
                        ffprobe_bin=None,
                        timeout_sec=float(timeout_sec),
                    )
                    temp_encoded.append(tmp_out)
                    vpaths.append(tmp_out)
            else:
                raise FFmpegCompileError(f"unknown segment kind: {kind!r}")

        flush_image_run()

        _stream_copy_join(vpaths, output, ffmpeg_bin=ffmpeg_bin, timeout_sec=timeout_sec)
        return {
            "output_path": str(output),
            "bytes": path_stat(output).st_size,
            "mode": "mixed_visual_timeline",
            "segment_count": len(segments),
            "input_count": len(vpaths),
            **enc.as_compile_meta(),
        }
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
