"""Local Kokoro TTS for chapter narration (optional ``pip install -e ".[kokoro]"``)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from director_api.config import Settings
from director_api.providers.optional_tts_pip import ensure_kokoro_importable
from director_api.services.kokoro_subtitles import (
    KOKORO_SUBTITLE_LANG_CODES,
    build_sentence_cues_from_tokens,
    cues_to_webvtt,
)
from ffmpeg_pipelines.probe import ffprobe_duration_seconds


def _select_device(settings: Settings) -> str:
    pref = (getattr(settings, "kokoro_device", None) or "").strip().lower()
    if pref in ("cpu", "cuda", "mps"):
        return pref
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _audio_to_numpy(chunk: Any):
    import numpy as np

    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().numpy()
    arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
    return arr


def synthesize_chapter_narration_mp3_kokoro(
    text: str,
    settings: Settings,
    *,
    voice: str,
    lang_code: str,
    speed: float,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 600.0,
) -> tuple[bytes, float, str | None]:
    """
    Returns (mp3_bytes, duration_sec, webvtt_or_none).
    WebVTT is produced for English ``a``/``b`` when the pipeline yields token timestamps.
    """
    ensure_kokoro_importable(settings)
    from kokoro import KPipeline

    body = (text or "").strip()
    if len(body) < 2:
        raise ValueError("empty narration text for Kokoro TTS")

    _kokoro_lang = frozenset("abefhijpz")
    lc_raw = (lang_code or "a").strip().lower()
    lc = lc_raw if lc_raw in _kokoro_lang else "a"
    repo_id = (getattr(settings, "kokoro_hf_repo_id", None) or "hexgrad/Kokoro-82M").strip() or "hexgrad/Kokoro-82M"
    device = _select_device(settings)
    voice = (voice or "af_bella").strip() or "af_bella"
    speed = float(speed) if speed else 1.0

    pipeline = KPipeline(lang_code=lc, repo_id=repo_id, device=device)

    import numpy as np

    chunks: list = []
    flat_tokens: list[dict] = []
    current_time = 0.0
    sample_rate = 24000

    for result in pipeline(body, voice=voice, speed=speed, split_pattern=r"\n\n+"):
        audio = _audio_to_numpy(result.audio)
        if audio.size == 0:
            continue
        chunk_dur = float(len(audio)) / float(sample_rate)
        chunk_start = current_time
        chunks.append(audio)

        for tok in getattr(result, "tokens", None) or []:
            st = getattr(tok, "start_ts", None) or 0.0
            et = getattr(tok, "end_ts", None) or 0.0
            flat_tokens.append(
                {
                    "start": chunk_start + float(st or 0.0),
                    "end": chunk_start + float(et or 0.0),
                    "text": getattr(tok, "text", "") or "",
                    "whitespace": getattr(tok, "whitespace", "") or "",
                }
            )
        current_time += chunk_dur

    if not chunks:
        raise RuntimeError("Kokoro produced no audio")

    wav_i16 = np.concatenate(chunks)
    wav_i16 = np.clip(wav_i16, -1.0, 1.0)
    pcm = (wav_i16 * 32767.0).astype(np.int16)

    webvtt: str | None = None
    if lc in KOKORO_SUBTITLE_LANG_CODES and flat_tokens:
        cues = build_sentence_cues_from_tokens(
            flat_tokens,
            max_subtitle_words=16,
            fallback_end_time=current_time,
        )
        if cues:
            webvtt = cues_to_webvtt(cues)

    ffmpeg_bin = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (ffprobe_bin or "ffprobe").strip() or "ffprobe"

    with tempfile.TemporaryDirectory(prefix="director_kokoro_") as td:
        tdir = Path(td)
        wav_path = tdir / "narration.wav"
        mp3_path = tdir / "narration.mp3"
        try:
            import soundfile as sf

            sf.write(str(wav_path), pcm, sample_rate, subtype="PCM_16")
        except ImportError as e:
            raise ValueError("soundfile is required for Kokoro output (install kokoro extra).") from e

        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(wav_path),
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(mp3_path),
            ],
            capture_output=True,
            text=True,
            timeout=min(timeout_sec, 900.0),
        )
        if proc.returncode != 0 or not mp3_path.is_file():
            tail = (proc.stderr or proc.stdout or "")[-3000:]
            raise RuntimeError(tail.strip() or "ffmpeg Kokoro mp3 encode failed")

        dur = ffprobe_duration_seconds(mp3_path, ffprobe_bin=ffprobe_bin, timeout_sec=min(120.0, timeout_sec))
        data = mp3_path.read_bytes()
        if len(data) < 64:
            raise RuntimeError("Kokoro produced empty MP3")
        return data, float(dur), webvtt
