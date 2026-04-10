"""ElevenLabs text-to-speech for chapter narration (chunked + ffmpeg concat)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

from director_api.config import Settings
from director_api.providers.speech_openai import (
    _bytes_look_like_mp3,
    chunk_narration_text,
    ffmpeg_concat_mp3_demuxer,
)
from ffmpeg_pipelines.probe import ffprobe_duration_seconds

_ELEVEN_MAX_CHARS = 2500  # stay under per-request limits
_ELEVEN_API = "https://api.elevenlabs.io/v1"


def _synthesize_chunk_mp3(
    api_key: str,
    voice_id: str,
    text: str,
    model_id: str,
    timeout_sec: float,
) -> bytes:
    url = f"{_ELEVEN_API}/text-to-speech/{voice_id}"
    body = {"text": text, "model_id": model_id}
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    with httpx.Client(timeout=timeout_sec) as client:
        r = client.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"elevenlabs HTTP {r.status_code}: {(r.text or '')[:800]}")
    data = r.content
    if not data or len(data) < 64:
        raise RuntimeError("elevenlabs returned empty audio")
    if not _bytes_look_like_mp3(data):
        head = data[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"elevenlabs returned non-MP3 audio. Body starts with: {head[:300]!r}")
    return data


def synthesize_chapter_narration_mp3_elevenlabs(
    text: str,
    settings: Settings,
    *,
    voice_id: str,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 600.0,
) -> tuple[bytes, float]:
    key = (settings.elevenlabs_api_key or "").strip()
    if not key:
        raise ValueError("ELEVENLABS_API_KEY (or elevenlabs_api_key in settings) is required for ElevenLabs narration")
    vid = (voice_id or getattr(settings, "elevenlabs_voice_id", None) or "").strip()
    if not vid:
        raise ValueError(
            "ElevenLabs voice_id is required: set elevenlabs_voice_id in workspace settings "
            "or project preferred_speech_provider to elevenlabs:<voice_id>"
        )
    model_id = (getattr(settings, "elevenlabs_model_id", None) or "eleven_multilingual_v2").strip() or "eleven_multilingual_v2"
    chunks = chunk_narration_text(text, max_len=_ELEVEN_MAX_CHARS)
    if not chunks:
        raise ValueError("empty narration text for TTS")

    ffmpeg_bin = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (ffprobe_bin or "ffprobe").strip() or "ffprobe"

    with tempfile.TemporaryDirectory(prefix="director_el_tts_") as td:
        tdir = Path(td)
        part_paths: list[Path] = []
        for idx, ch in enumerate(chunks):
            raw = _synthesize_chunk_mp3(key, vid, ch, model_id, timeout_sec=min(300.0, timeout_sec))
            p = tdir / f"part_{idx:04d}.mp3"
            p.write_bytes(raw)
            part_paths.append(p)

        if len(part_paths) == 1:
            merged = part_paths[0]
        else:
            lst = tdir / "concat.txt"
            lst.write_text("\n".join(f"file '{p.as_posix()}'" for p in part_paths), encoding="utf-8")
            merged = tdir / "merged.mp3"
            ffmpeg_concat_mp3_demuxer(ffmpeg_bin, lst, merged, timeout_sec=timeout_sec)

        dur = ffprobe_duration_seconds(merged, ffprobe_bin=ffprobe_bin, timeout_sec=min(120.0, timeout_sec))
        data = merged.read_bytes()
        return data, float(dur)
