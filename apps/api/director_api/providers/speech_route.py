"""Resolve which TTS backend to use for chapter narration (cloud + optional local)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from director_api.config import Settings
from director_api.providers.speech_openai import OPENAI_TTS_VOICES


def _chatterbox_ref_path(rest: str, settings: Settings) -> str:
    r = (rest or "").strip()
    if r:
        return r
    return (getattr(settings, "chatterbox_voice_ref_path", None) or "").strip()


def resolve_speech_narration_route(
    project_preferred_speech_provider: str | None,
    settings: Settings,
) -> tuple[str, dict[str, Any]]:
    """
    Returns (provider, options).

    provider: openai | elevenlabs | gemini | kokoro | chatterbox_turbo | chatterbox_mtl | placeholder
    options:
      openai: voice (str)
      elevenlabs: voice_id (str)
      gemini: voice (str)
      kokoro: voice, lang_code, speed
      chatterbox_turbo: ref_path (str filesystem or file:// URL)
      chatterbox_mtl: ref_path, language_id (str)
      placeholder: {} (FFmpeg ding — only when project explicitly requests placeholder/ding)
    """
    raw = (project_preferred_speech_provider or getattr(settings, "active_speech_provider", None) or "openai").strip()
    rl = raw.lower()

    if rl in ("placeholder", "ding", "budget_tts"):
        return "placeholder", {}

    # Legacy: workspace stored OpenAI voice name as "provider" (alloy, nova, …).
    if rl in OPENAI_TTS_VOICES:
        return "openai", {"voice": rl}

    if rl in ("openai", "openai_tts", ""):
        v = (getattr(settings, "openai_tts_voice", None) or "alloy").strip().lower()
        if v not in OPENAI_TTS_VOICES:
            v = "alloy"
        return "openai", {"voice": v}

    if rl.startswith("openai:"):
        v = raw.split(":", 1)[1].strip().lower()
        if v not in OPENAI_TTS_VOICES:
            v = (getattr(settings, "openai_tts_voice", None) or "alloy").strip().lower()
            if v not in OPENAI_TTS_VOICES:
                v = "alloy"
        return "openai", {"voice": v}

    if rl in ("elevenlabs", "11labs", "eleven"):
        vid = (getattr(settings, "elevenlabs_voice_id", None) or "").strip()
        return "elevenlabs", {"voice_id": vid}

    if rl.startswith("elevenlabs:"):
        vid = raw.split(":", 1)[1].strip() or (getattr(settings, "elevenlabs_voice_id", None) or "").strip()
        return "elevenlabs", {"voice_id": vid}

    if rl in ("gemini", "google", "google_tts"):
        v = (getattr(settings, "gemini_tts_voice", None) or "Kore").strip() or "Kore"
        return "gemini", {"voice": v}

    if rl.startswith("gemini:"):
        v = raw.split(":", 1)[1].strip() or (getattr(settings, "gemini_tts_voice", None) or "Kore")
        return "gemini", {"voice": v.strip() or "Kore"}

    if rl in ("kokoro", "local_kokoro"):
        voice = (getattr(settings, "kokoro_voice", None) or "af_bella").strip() or "af_bella"
        lang = (getattr(settings, "kokoro_lang_code", None) or "a").strip() or "a"
        speed = float(getattr(settings, "kokoro_speed", 1.0) or 1.0)
        return "kokoro", {"voice": voice, "lang_code": lang, "speed": speed}

    if rl.startswith("kokoro:"):
        voice = raw.split(":", 1)[1].strip() or (getattr(settings, "kokoro_voice", None) or "af_bella")
        voice = (voice or "af_bella").strip() or "af_bella"
        lang = (getattr(settings, "kokoro_lang_code", None) or "a").strip() or "a"
        speed = float(getattr(settings, "kokoro_speed", 1.0) or 1.0)
        return "kokoro", {"voice": voice, "lang_code": lang, "speed": speed}

    if rl in ("chatterbox", "chatterbox_turbo", "resemble_turbo"):
        ref = _chatterbox_ref_path("", settings)
        return "chatterbox_turbo", {"ref_path": ref}

    if rl.startswith("chatterbox_turbo:"):
        ref = _chatterbox_ref_path(raw.split(":", 1)[1], settings)
        return "chatterbox_turbo", {"ref_path": ref}

    if rl in ("chatterbox_mtl", "chatterbox_multilingual", "resemble_mtl"):
        ref = _chatterbox_ref_path("", settings)
        lang = (getattr(settings, "chatterbox_mtl_language_id", None) or "en").strip() or "en"
        return "chatterbox_mtl", {"ref_path": ref, "language_id": lang}

    if rl.startswith("chatterbox_mtl:"):
        tail = raw.split(":", 2)
        if len(tail) == 2:
            lang = tail[1].strip() or "en"
            ref = _chatterbox_ref_path("", settings)
        else:
            lang = tail[1].strip() or "en"
            ref = _chatterbox_ref_path(tail[2], settings)
        return "chatterbox_mtl", {"ref_path": ref, "language_id": lang}

    # Unknown → OpenAI default
    v = (getattr(settings, "openai_tts_voice", None) or "alloy").strip().lower()
    if v not in OPENAI_TTS_VOICES:
        v = "alloy"
    return "openai", {"voice": v}


def resolve_chatterbox_ref_to_path(ref_path: str, *, storage_root: Path) -> Path:
    """Map ``file://…``, absolute path, or storage-relative key to a path."""
    from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file

    s = (ref_path or "").strip()
    if not s:
        raise ValueError("Chatterbox reference path is empty (set project preferred_speech_provider or CHATTERBOX_VOICE_REF_PATH).")
    if s.startswith("file:"):
        p = path_from_storage_url(s, storage_root=storage_root)
        if p is None or not path_is_readable_file(p):
            raise ValueError(f"Chatterbox reference file not found: {s}")
        return p
    p = Path(s)
    if path_is_readable_file(p):
        return p.resolve()
    rel = path_from_storage_url(s.lstrip("/"), storage_root=storage_root)
    if rel is not None and path_is_readable_file(rel):
        return rel
    raise ValueError(f"Chatterbox reference audio not found: {s}")
