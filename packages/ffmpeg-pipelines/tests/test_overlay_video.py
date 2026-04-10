import shutil
import subprocess
from pathlib import Path

import pytest

from ffmpeg_pipelines.filter_probe import ffmpeg_filter_available
from ffmpeg_pipelines.overlay_video import build_overlay_filter_chain, burn_overlays_on_video


def test_build_overlay_filter_chain_chains_labels():
    built = build_overlay_filter_chain(
        [
            {"type": "title_card", "start_sec": 0, "end_sec": 1, "text": "Hi"},
            {"type": "lower_third", "start_sec": 1, "end_sec": 2, "text": "A", "subtext": "B"},
        ]
    )
    assert built is not None
    chain, last = built
    assert "[0:v]" in chain
    assert "[ov0]" in chain
    assert "[ov1]" in chain
    assert last == "ov1"
    assert "between(t\\,0.0\\,1.0)" in chain


def test_build_overlay_filter_chain_skips_invalid_window():
    assert build_overlay_filter_chain([{"type": "title_card", "start_sec": 2, "end_sec": 1, "text": "x"}]) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
@pytest.mark.skipif(
    not ffmpeg_filter_available("ffmpeg", "drawtext"),
    reason="ffmpeg without drawtext (libfreetype)",
)
def test_burn_overlays_produces_mp4(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    out = tmp_path / "out.mp4"
    meta = burn_overlays_on_video(
        src,
        out,
        [{"type": "title_card", "start_sec": 0, "end_sec": 0.5, "text": "T"}],
        ffmpeg_bin="ffmpeg",
        timeout_sec=60.0,
    )
    assert out.is_file()
    assert meta.get("mode") == "fine_cut_overlays"
