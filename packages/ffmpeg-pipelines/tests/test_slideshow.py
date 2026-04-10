import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines import slideshow as slideshow_mod
from ffmpeg_pipelines.slideshow import compile_image_slideshow


def test_windows_slideshow_batch_cap_avoids_argv_overflow():
    """Pan + xfade filter graphs are huge; small batches avoid CreateProcess WinError 206."""
    if os.name == "nt":
        assert slideshow_mod._SLIDESHOW_BATCH_CAP_NT <= 8

requires_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")


@requires_ffmpeg
def test_compile_image_slideshow_two_frames(tmp_path: Path):
    for name in ("a.png", "b.png"):
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x64:d=0.01",
                "-frames:v",
                "1",
                str(tmp_path / name),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
    out = tmp_path / "out.mp4"
    meta = compile_image_slideshow(
        [(tmp_path / "a.png", 0.2), (tmp_path / "b.png", 0.2)],
        out,
        timeout_sec=120.0,
    )
    assert out.is_file()
    assert meta["bytes"] > 1000
    assert meta["slide_count"] == 2
    assert meta.get("slow_zoom") is False


def test_compile_image_slideshow_rejects_missing_file(tmp_path: Path):
    missing = tmp_path / "nope.png"
    out = tmp_path / "out.mp4"
    with pytest.raises(FFmpegCompileError, match="not found"):
        compile_image_slideshow([(missing, 1.0)], out)
