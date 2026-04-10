"""Placeholder narration audio — short ding + low tone, length scales with script size (no cloud TTS)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from ffmpeg_pipelines.probe import ffprobe_duration_seconds


def placeholder_narration_duration_sec(text: str) -> float:
    n = len((text or "").strip())
    # Hands-off scripts can be long; cap so FFmpeg + mux stay reasonable.
    return max(1.5, min(300.0, 2.0 + n / 80.0))


def synthesize_placeholder_narration_mp3(
    text: str,
    *,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 300.0,
) -> tuple[bytes, float]:
    """Two-tone ding (~0.2s) + quiet 220 Hz bed for the remaining duration → MP3 bytes."""
    ff = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    fp = (ffprobe_bin or "ffprobe").strip() or "ffprobe"
    dur = placeholder_narration_duration_sec(text)
    d1 = min(0.12, max(0.04, dur * 0.02))
    d2 = min(0.12, max(0.05, dur * 0.025))
    rest = max(0.05, dur - d1 - d2)
    # High “ding” then slightly lower “dong”, then quiet carrier for timeline alignment.
    filt = (
        "[0:a][1:a]concat=n=2:v=0:a=1[ding];"
        "[2:a]volume=-28dB[bed];"
        "[ding][bed]concat=n=2:v=0:a=1[out]"
    )
    cmd = [
        ff,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=1046.5:sample_rate=44100:duration={d1:.4f}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=784:sample_rate=44100:duration={d2:.4f}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=220:sample_rate=44100:duration={rest:.4f}",
        "-filter_complex",
        filt,
        "-map",
        "[out]",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        "-f",
        "mp3",
    ]
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        cmd.append(str(out_path))
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace")[:2000]
            raise RuntimeError(f"placeholder TTS ffmpeg failed ({r.returncode}): {err}")
        data = out_path.read_bytes()
        if len(data) < 64:
            raise RuntimeError("placeholder TTS produced empty mp3")
        measured = ffprobe_duration_seconds(
            Path(out_path),
            ffprobe_bin=fp,
            timeout_sec=min(60.0, float(timeout_sec)),
        )
        return data, float(measured)
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
