import shutil
import subprocess
from pathlib import Path

import pytest

from ffmpeg_pipelines.audio_slot import normalize_audio_to_duration


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_normalize_audio_to_duration(tmp_path: Path) -> None:
    src = tmp_path / "in.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "2",
            str(src),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    out = tmp_path / "out.m4a"
    normalize_audio_to_duration(src, out, 0.75, ffmpeg_bin="ffmpeg", timeout_sec=60.0)
    assert out.is_file() and out.stat().st_size > 100
