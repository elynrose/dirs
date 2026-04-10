"""Local Chatterbox TTS (voice clone); optional ``pip install -e ".[chatterbox]"`` + vendored package."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from director_api.config import Settings
from director_api.providers.optional_tts_pip import ensure_chatterbox_importable
from ffmpeg_pipelines.paths import path_is_readable_file
from ffmpeg_pipelines.probe import ffprobe_duration_seconds

Variant = Literal["turbo", "mtl"]


def _select_device(settings: Settings) -> str:
    import torch

    pref = (getattr(settings, "chatterbox_device", None) or "").strip().lower()
    if pref in ("cpu", "cuda", "mps"):
        return pref
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def synthesize_chapter_narration_mp3_chatterbox(
    text: str,
    settings: Settings,
    *,
    variant: Variant,
    ref_audio_path: Path,
    language_id: str | None,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 600.0,
) -> tuple[bytes, float]:
    body = (text or "").strip()
    if len(body) < 2:
        raise ValueError("empty narration text for Chatterbox TTS")
    ref = ref_audio_path.expanduser().resolve()
    if not path_is_readable_file(ref):
        raise ValueError(f"Chatterbox reference audio not found: {ref}")

    ensure_chatterbox_importable(settings)

    device = _select_device(settings)
    ffmpeg_bin = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (ffprobe_bin or "ffprobe").strip() or "ffprobe"

    import torchaudio as ta

    if variant == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        model = ChatterboxTurboTTS.from_pretrained(device=device)
        wav_tensor = model.generate(str(body), audio_prompt_path=str(ref))
        sr = int(model.sr)
    else:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        lang = (language_id or getattr(settings, "chatterbox_mtl_language_id", None) or "en").strip().lower()
        model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        wav_tensor = model.generate(str(body), language_id=lang, audio_prompt_path=str(ref))
        sr = int(model.sr)

    if wav_tensor.dim() == 1:
        wav_tensor = wav_tensor.unsqueeze(0)

    with tempfile.TemporaryDirectory(prefix="director_chatterbox_") as td:
        tdir = Path(td)
        wav_path = tdir / "narration.wav"
        mp3_path = tdir / "narration.mp3"
        ta.save(str(wav_path), wav_tensor.cpu().squeeze(0), sr)

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
            raise RuntimeError(tail.strip() or "ffmpeg Chatterbox mp3 encode failed")

        dur = ffprobe_duration_seconds(mp3_path, ffprobe_bin=ffprobe_bin, timeout_sec=min(120.0, timeout_sec))
        data = mp3_path.read_bytes()
        if len(data) < 64:
            raise RuntimeError("Chatterbox produced empty MP3")
        return data, float(dur)
