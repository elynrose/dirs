import shutil
import subprocess
from pathlib import Path

import pytest

from ffmpeg_pipelines.still_to_video import encode_image_to_mp4


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_encode_image_to_mp4(tmp_path: Path) -> None:
    img = tmp_path / "x.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64",
            "-frames:v",
            "1",
            str(img),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    out = tmp_path / "clip.mp4"
    meta = encode_image_to_mp4(
        img,
        out,
        duration_sec=0.6,
        width=320,
        height=240,
        fps=15,
        ffmpeg_bin="ffmpeg",
        timeout_sec=60.0,
    )
    assert out.is_file()
    assert meta.get("bytes", 0) > 100
    assert meta.get("slow_zoom") is False
