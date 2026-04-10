"""Tests for compile_video_concat (requires ffmpeg on PATH)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import ffmpeg_pipelines.video_chain as video_chain
from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.video_chain import compile_video_concat


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")


def _tiny_mp4(path: Path, *, hue: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=s=64x48:r=30,hue=h={hue}",
            "-t",
            "0.08",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def test_concat_small_single_invocation(tmp_path: Path) -> None:
    _require_ffmpeg()
    paths = []
    for i in range(3):
        p = tmp_path / f"s{i}.mp4"
        _tiny_mp4(p, hue=float(i) * 40)
        paths.append(p)
    out = tmp_path / "out.mp4"
    meta = compile_video_concat(
        paths,
        out,
        width=320,
        height=180,
        fps=30,
        timeout_sec=120.0,
    )
    assert meta["input_count"] == 3
    assert meta.get("chunked_concat") is False
    assert out.is_file() and out.stat().st_size > 64


def test_concat_forces_chunking_with_low_cap(tmp_path: Path) -> None:
    """Batches ffmpeg when input count exceeds a tiny cap (simulates Windows limits)."""
    _require_ffmpeg()
    old = video_chain._MAX_FFMPEG_INPUTS_PER_INVOCATION
    video_chain._MAX_FFMPEG_INPUTS_PER_INVOCATION = 2
    try:
        paths = []
        for i in range(6):
            p = tmp_path / f"c{i}.mp4"
            _tiny_mp4(p, hue=float(i) * 30)
            paths.append(p)
        out = tmp_path / "chunked.mp4"
        meta = compile_video_concat(
            paths,
            out,
            width=320,
            height=180,
            fps=30,
            timeout_sec=300.0,
        )
        assert meta["input_count"] == 6
        assert meta.get("chunked_concat") is True
        assert out.is_file() and out.stat().st_size > 64
    finally:
        video_chain._MAX_FFMPEG_INPUTS_PER_INVOCATION = old


def test_concat_empty_raises() -> None:
    with pytest.raises(FFmpegCompileError, match="no video"):
        compile_video_concat([], Path("nope.mp4"))
