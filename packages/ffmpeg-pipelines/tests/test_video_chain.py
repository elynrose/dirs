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


def _ffprobe_stream_types(path: Path) -> list[str]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return lines


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe not on PATH")
def test_stream_copy_join_output_is_video_only(tmp_path: Path) -> None:
    """Joined rough-cut partials must not carry audio (e.g. stock clip sound)."""
    parts: list[Path] = []
    for i in range(2):
        p = tmp_path / f"join{i}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=s=64x48:r=30,hue=h={float(i) * 40}",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "0.08",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(p),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        parts.append(p)
        assert "audio" in _ffprobe_stream_types(p)

    out = tmp_path / "joined.mp4"
    video_chain._stream_copy_join(parts, out, ffmpeg_bin="ffmpeg", timeout_sec=120.0)
    assert out.is_file() and out.stat().st_size > 64
    types = _ffprobe_stream_types(out)
    assert "video" in types
    assert "audio" not in types
