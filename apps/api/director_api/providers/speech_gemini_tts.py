"""Gemini API text-to-speech (preview TTS models) — PCM output, ffmpeg to MP3."""

from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from director_api.config import Settings
from director_api.providers.gemini_rest import GEMINI_API_BASE
from director_api.providers.speech_openai import chunk_narration_text
from ffmpeg_pipelines.probe import ffprobe_duration_seconds

# Preview TTS models return LINEAR16 mono PCM at 24 kHz per Google docs.
_GEMINI_PCM_RATE = 24000
_GEMINI_MAX_CHARS = 3500


def _chunk_gemini(text: str) -> list[str]:
    return chunk_narration_text(text, max_len=_GEMINI_MAX_CHARS)


def _extract_inline_audio(data: dict[str, Any]) -> tuple[bytes, str | None]:
    cands = data.get("candidates")
    if not isinstance(cands, list) or not cands:
        raise RuntimeError("gemini TTS: no candidates")
    c0 = cands[0]
    if not isinstance(c0, dict):
        raise RuntimeError("gemini TTS: invalid candidate")
    content = c0.get("content")
    if not isinstance(content, dict):
        raise RuntimeError("gemini TTS: no content")
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise RuntimeError("gemini TTS: no parts")
    p0 = parts[0]
    if not isinstance(p0, dict):
        raise RuntimeError("gemini TTS: invalid part")
    inline = p0.get("inlineData") or p0.get("inline_data")
    if not isinstance(inline, dict):
        raise RuntimeError("gemini TTS: no inline audio data")
    b64 = inline.get("data")
    if not isinstance(b64, str) or not b64.strip():
        raise RuntimeError("gemini TTS: empty audio payload")
    mime = inline.get("mimeType") or inline.get("mime_type")
    return base64.b64decode(b64), mime if isinstance(mime, str) else None


def _pcm_to_mp3(
    pcm: bytes,
    ffmpeg_bin: str,
    out_path: Path,
    timeout_sec: float,
) -> None:
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(_GEMINI_PCM_RATE),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(out_path),
        ],
        input=pcm,
        capture_output=True,
        timeout=timeout_sec,
    )
    if proc.returncode != 0 or not out_path.is_file():
        tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(tail.strip() or "ffmpeg gemini pcm→mp3 failed")


def synthesize_chapter_narration_mp3_gemini(
    text: str,
    settings: Settings,
    *,
    voice_name: str,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 600.0,
) -> tuple[bytes, float]:
    api_key = (settings.gemini_api_key or "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required for Gemini narration TTS")
    model = (getattr(settings, "gemini_tts_model", None) or "gemini-2.5-flash-preview-tts").strip()
    model_id = model.lstrip("models/")
    voice = (voice_name or getattr(settings, "gemini_tts_voice", None) or "Kore").strip() or "Kore"

    chunks = _chunk_gemini(text)
    if not chunks:
        raise ValueError("empty narration text for TTS")

    ffmpeg_bin = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (ffprobe_bin or "ffprobe").strip() or "ffprobe"
    url = f"{GEMINI_API_BASE}/models/{model_id}:generateContent"

    with tempfile.TemporaryDirectory(prefix="director_gemini_tts_") as td:
        tdir = Path(td)
        part_paths: list[Path] = []
        per = min(180.0, timeout_sec)
        for idx, ch in enumerate(chunks):
            body: dict[str, Any] = {
                "contents": [{"parts": [{"text": ch}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
                    },
                },
            }
            with httpx.Client(timeout=per) as client:
                r = client.post(url, params={"key": api_key}, json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"gemini TTS HTTP {r.status_code}: {(r.text or '')[:900]}")
            data = r.json()
            if not isinstance(data, dict):
                raise RuntimeError("gemini TTS: response is not JSON object")
            pcm, _mime = _extract_inline_audio(data)
            mp3_p = tdir / f"part_{idx:04d}.mp3"
            _pcm_to_mp3(pcm, ffmpeg_bin, mp3_p, timeout_sec=min(120.0, timeout_sec))
            part_paths.append(mp3_p)

        if len(part_paths) == 1:
            merged = part_paths[0]
        else:
            lst = tdir / "concat.txt"
            lst.write_text("\n".join(f"file '{p.as_posix()}'" for p in part_paths), encoding="utf-8")
            merged = tdir / "merged.mp3"
            proc = subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(lst),
                    "-c",
                    "copy",
                    str(merged),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            if proc.returncode != 0 or not merged.is_file():
                tail = (proc.stderr or proc.stdout or "")[-3000:]
                raise RuntimeError(tail.strip() or "ffmpeg Gemini TTS concat failed")

        dur = ffprobe_duration_seconds(merged, ffprobe_bin=ffprobe_bin, timeout_sec=min(120.0, timeout_sec))
        data_b = merged.read_bytes()
        if len(data_b) < 64:
            raise RuntimeError("Gemini TTS produced empty MP3")
        return data_b, float(dur)
