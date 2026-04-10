"""Tests for compile_mixed_visual_timeline (requires ffmpeg on PATH)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.mixed_timeline import compile_mixed_visual_timeline
from ffmpeg_pipelines.probe import ffprobe_duration_seconds
from ffmpeg_pipelines.still_to_video import encode_image_to_mp4


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")


def _solid_png(path: Path, color: str = "red") -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x64:d=0.01",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def test_mixed_image_and_video(tmp_path: Path) -> None:
    _require_ffmpeg()
    img = tmp_path / "s.png"
    _solid_png(img)
    seg_vid = tmp_path / "seg.mp4"
    encode_image_to_mp4(img, seg_vid, duration_sec=0.2, width=320, height=180, timeout_sec=60.0)
    out = tmp_path / "mixed.mp4"
    meta = compile_mixed_visual_timeline(
        [("image", img, 0.25), ("video", seg_vid, None)],
        out,
        width=320,
        height=180,
        timeout_sec=120.0,
    )
    assert meta.get("mode") == "mixed_visual_timeline"
    assert meta.get("segment_count") == 2
    assert out.is_file() and out.stat().st_size > 64


def test_mixed_24fps_video_with_image(tmp_path: Path) -> None:
    """concat filter requires uniform fps; raw scene clips may be 24fps while still encodes are 30."""
    _require_ffmpeg()
    img = tmp_path / "s.png"
    _solid_png(img)
    v24 = tmp_path / "v24.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x240:r=24",
            "-t",
            "0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(v24),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    out = tmp_path / "mixed_fps.mp4"
    meta = compile_mixed_visual_timeline(
        [("image", img, 0.25), ("video", v24, None)],
        out,
        width=320,
        height=180,
        timeout_sec=120.0,
    )
    assert meta.get("mode") == "mixed_visual_timeline"
    assert out.is_file() and out.stat().st_size > 64


def test_mixed_batches_three_consecutive_static_images(tmp_path: Path) -> None:
    """Consecutive images should go through one static slideshow encode (assembly)."""
    _require_ffmpeg()
    paths = []
    for i, color in enumerate(("red", "green", "blue")):
        p = tmp_path / f"s{i}.png"
        _solid_png(p, color=color)
        paths.append(p)
    out = tmp_path / "three_stills.mp4"
    meta = compile_mixed_visual_timeline(
        [
            ("image", paths[0], 0.2),
            ("image", paths[1], 0.25),
            ("image", paths[2], 0.15),
        ],
        out,
        width=320,
        height=180,
        timeout_sec=180.0,
    )
    assert meta.get("mode") == "mixed_visual_timeline"
    assert meta.get("segment_count") == 3
    assert out.is_file() and out.stat().st_size > 64
    d = ffprobe_duration_seconds(out, ffprobe_bin="ffprobe", timeout_sec=60.0)
    assert 0.55 <= d <= 0.75, f"expected ~0.6s total, got {d}"


def test_mixed_requires_image_duration(tmp_path: Path) -> None:
    _require_ffmpeg()
    img = tmp_path / "s.png"
    _solid_png(img)
    out = tmp_path / "bad.mp4"
    with pytest.raises(FFmpegCompileError, match="duration"):
        compile_mixed_visual_timeline([("image", img, None)], out, width=320, height=180, timeout_sec=60.0)
