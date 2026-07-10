"""Local Kokoro TTS for chapter narration.

Runs in-process when ``kokoro`` + ``soundfile`` are importable (e.g. a Python 3.11 worker).
Otherwise, if a local-TTS sidecar venv is configured (``tts_sidecar_python``), the torch-based
``KPipeline`` inference is offloaded to that venv as a subprocess (see ``director_api/tts/
kokoro_render.py``). This keeps Kokoro working on runtimes without torch (e.g. Python 3.14),
mirroring the GPU Ken Burns sidecar. The torch-free work (WebVTT + MP3 encode) stays here.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import structlog

from director_api.config import Settings
from director_api.providers.optional_tts_pip import ensure_kokoro_importable
from director_api.services.kokoro_subtitles import (
    KOKORO_SUBTITLE_LANG_CODES,
    build_sentence_cues_from_tokens,
    cues_to_webvtt,
)
from ffmpeg_pipelines.probe import ffprobe_duration_seconds

log = structlog.get_logger(__name__)

_KOKORO_LANG = frozenset("abefhijpz")


def _kokoro_importable() -> bool:
    return (
        importlib.util.find_spec("kokoro") is not None
        and importlib.util.find_spec("soundfile") is not None
    )


def _tts_sidecar_python(settings: Settings) -> str | None:
    p = str(getattr(settings, "tts_sidecar_python", "") or "").strip()
    if not p:
        return None
    return p if Path(p).is_file() else None


def _sidecar_script_path() -> Path:
    import director_api

    return Path(director_api.__file__).resolve().parent / "tts" / "kokoro_render.py"


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
    return np.asarray(chunk, dtype=np.float32).reshape(-1)


def _render_in_process(
    body: str,
    settings: Settings,
    *,
    voice: str,
    lc: str,
    speed: float,
    repo_id: str,
    wav_path: Path,
    sample_rate: int = 24000,
) -> tuple[list[dict], float]:
    """Run KPipeline in this process; write ``wav_path`` and return (flat_tokens, total_sec)."""
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    pipeline = KPipeline(lang_code=lc, repo_id=repo_id, device=_select_device(settings))
    chunks: list = []
    flat_tokens: list[dict] = []
    current_time = 0.0
    for result in pipeline(body, voice=voice, speed=speed, split_pattern=r"\n\n+"):
        audio = _audio_to_numpy(result.audio)
        if audio.size == 0:
            continue
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
        current_time += float(len(audio)) / float(sample_rate)

    if not chunks:
        raise RuntimeError("Kokoro produced no audio")
    pcm = (np.clip(np.concatenate(chunks), -1.0, 1.0) * 32767.0).astype(np.int16)
    sf.write(str(wav_path), pcm, sample_rate, subtype="PCM_16")
    return flat_tokens, current_time


def _render_via_sidecar(
    sidecar_python: str,
    body: str,
    settings: Settings,
    *,
    voice: str,
    lc: str,
    speed: float,
    repo_id: str,
    wav_path: Path,
    work_dir: Path,
    timeout_sec: float,
) -> tuple[list[dict], float]:
    """Offload KPipeline to the sidecar venv; write ``wav_path`` and return (flat_tokens, total_sec)."""
    script = _sidecar_script_path()
    if not script.is_file():
        raise RuntimeError(f"Kokoro sidecar script missing: {script}")
    text_file = work_dir / "narration.txt"
    tokens_out = work_dir / "tokens.json"
    text_file.write_text(body, encoding="utf-8")
    device = (getattr(settings, "kokoro_device", None) or "").strip().lower()
    cmd = [
        sidecar_python,
        str(script),
        "--text-file", str(text_file),
        "--wav-out", str(wav_path),
        "--tokens-out", str(tokens_out),
        "--voice", voice,
        "--lang", lc,
        "--speed", f"{speed:.4f}",
        "--repo-id", repo_id,
    ]
    if device in ("cpu", "cuda", "mps"):
        cmd += ["--device", device]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=min(timeout_sec, 1800.0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"Kokoro sidecar failed to start: {e}") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-3000:]
        raise RuntimeError(tail.strip() or f"Kokoro sidecar exited {proc.returncode}")
    if not wav_path.is_file() or not tokens_out.is_file():
        raise RuntimeError("Kokoro sidecar produced no output")
    meta = json.loads(tokens_out.read_text(encoding="utf-8"))
    flat_tokens = meta.get("tokens") if isinstance(meta, dict) else None
    total = float(meta.get("duration") or 0.0) if isinstance(meta, dict) else 0.0
    return (flat_tokens if isinstance(flat_tokens, list) else []), total


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
    body = (text or "").strip()
    if len(body) < 2:
        raise ValueError("empty narration text for Kokoro TTS")

    lc_raw = (lang_code or "a").strip().lower()
    lc = lc_raw if lc_raw in _KOKORO_LANG else "a"
    repo_id = (getattr(settings, "kokoro_hf_repo_id", None) or "hexgrad/Kokoro-82M").strip() or "hexgrad/Kokoro-82M"
    voice = (voice or "af_bella").strip() or "af_bella"
    speed = float(speed) if speed else 1.0
    sample_rate = 24000

    ffmpeg_bin = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (ffprobe_bin or "ffprobe").strip() or "ffprobe"

    with tempfile.TemporaryDirectory(prefix="director_kokoro_") as td:
        tdir = Path(td)
        wav_path = tdir / "narration.wav"
        mp3_path = tdir / "narration.mp3"

        if _kokoro_importable():
            flat_tokens, total = _render_in_process(
                body, settings, voice=voice, lc=lc, speed=speed, repo_id=repo_id,
                wav_path=wav_path, sample_rate=sample_rate,
            )
        else:
            sidecar_python = _tts_sidecar_python(settings)
            if sidecar_python is None:
                # No in-process kokoro and no sidecar: let ensure_* raise the helpful message
                # (or pip-install into this venv if the operator enabled TTS_AUTO_PIP_INSTALL).
                ensure_kokoro_importable(settings)
                flat_tokens, total = _render_in_process(
                    body, settings, voice=voice, lc=lc, speed=speed, repo_id=repo_id,
                    wav_path=wav_path, sample_rate=sample_rate,
                )
            else:
                log.info("kokoro_tts_via_sidecar", python=sidecar_python)
                flat_tokens, total = _render_via_sidecar(
                    sidecar_python, body, settings, voice=voice, lc=lc, speed=speed,
                    repo_id=repo_id, wav_path=wav_path, work_dir=tdir, timeout_sec=timeout_sec,
                )

        if not wav_path.is_file():
            raise RuntimeError("Kokoro produced no audio")

        webvtt: str | None = None
        if lc in KOKORO_SUBTITLE_LANG_CODES and flat_tokens:
            cues = build_sentence_cues_from_tokens(
                flat_tokens,
                max_subtitle_words=16,
                fallback_end_time=total,
            )
            if cues:
                webvtt = cues_to_webvtt(cues)

        proc = subprocess.run(
            [
                ffmpeg_bin, "-y", "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path),
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
