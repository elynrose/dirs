"""Tests for concat_audio_files (requires ffmpeg on PATH)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ffmpeg_pipelines.audio_concat import concat_audio_files
from ffmpeg_pipelines.errors import FFmpegCompileError


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")


def _sine_m4a(path: Path, duration_sec: float = 0.15) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=48000:duration={duration_sec}",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def test_concat_audio_files_two_inputs(tmp_path: Path) -> None:
    _require_ffmpeg()
    a1 = tmp_path / "a.m4a"
    a2 = tmp_path / "b.m4a"
    _sine_m4a(a1, 0.12)
    _sine_m4a(a2, 0.12)
    out = tmp_path / "out.m4a"
    meta = concat_audio_files([a1, a2], out, timeout_sec=60.0)
    assert meta["input_count"] == 2
    assert meta["mode"] == "audio_concat"
    assert out.is_file() and out.stat().st_size > 64


def test_concat_audio_files_empty_raises() -> None:
    _require_ffmpeg()
    with pytest.raises(FFmpegCompileError, match="no audio"):
        concat_audio_files([], Path("/tmp/x.m4a"))
