"""Per-scene narration TTS (same engines as chapter narration, stored on ``NarrationTrack.scene_id``)."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from director_api.db.models import Chapter, Job, NarrationTrack, Project, Scene, UsageRecord
from director_api.providers.speech_chatterbox import synthesize_chapter_narration_mp3_chatterbox
from director_api.providers.speech_elevenlabs import synthesize_chapter_narration_mp3_elevenlabs
from director_api.providers.speech_gemini_tts import synthesize_chapter_narration_mp3_gemini
from director_api.providers.speech_kokoro import synthesize_chapter_narration_mp3_kokoro
from director_api.providers.speech_openai import synthesize_chapter_narration_mp3
from director_api.providers.speech_placeholder import synthesize_placeholder_narration_mp3
from director_api.providers.speech_route import resolve_chatterbox_ref_to_path, resolve_speech_narration_route
from director_api.services.research_service import sanitize_jsonb_text
from director_api.services.scene_timeline_duration import (
    bump_scene_planned_duration_for_narration,
    scene_vo_tail_padding_sec_from_settings,
)
from director_api.services.usage_credits import compute_request_credits
from director_api.storage.filesystem import FilesystemStorage
from ffmpeg_pipelines.paths import path_is_readable_file


def run_scene_narration_tts_job(db: Any, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    sid = uuid.UUID(str(payload["scene_id"]))
    tenant = str(payload.get("tenant_id") or settings.default_tenant_id)
    sc = db.get(Scene, sid)
    if not sc:
        raise ValueError("scene not found")
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise ValueError("chapter not found")
    project = db.get(Project, ch.project_id)
    if not project or project.tenant_id != tenant:
        raise ValueError("project not found")

    body = sanitize_jsonb_text((sc.narration_text or "").strip(), 12_000)
    if len(body) < 2:
        raise ValueError("scene has no narration text to synthesize — save Scene script (VO) first")

    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        raise ValueError("ffmpeg not found on PATH (required for TTS)")

    provider, opts = resolve_speech_narration_route(project.preferred_speech_provider, settings)
    ffprobe_bin = (settings.ffprobe_bin or "ffprobe").strip() or "ffprobe"
    timeout_sec = float(settings.ffmpeg_timeout_sec)

    storage = FilesystemStorage(settings.local_storage_root)
    storage_root = Path(settings.local_storage_root).resolve()
    vtt_key = f"narrations/{project.id}/scenes/{sc.id}.vtt"
    vtt_disk = storage.get_path(vtt_key)
    try:
        if path_is_readable_file(vtt_disk):
            vtt_disk.unlink()
    except OSError:
        pass

    webvtt: str | None = None
    if provider == "placeholder":
        mp3_bytes, dur = synthesize_placeholder_narration_mp3(
            body,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=min(timeout_sec, 600.0),
        )
        voice_cfg = {"provider": "placeholder", "kind": "ffmpeg_ding"}
        usage_provider = "placeholder"
        usage_service = "narration_tts_placeholder_scene"
        usage_meta = {"chapter_id": str(ch.id), "scene_id": str(sc.id)}
    elif provider == "kokoro":
        mp3_bytes, dur, webvtt = synthesize_chapter_narration_mp3_kokoro(
            body,
            settings,
            voice=str(opts.get("voice") or "af_bella"),
            lang_code=str(opts.get("lang_code") or "a"),
            speed=float(opts.get("speed") or 1.0),
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        voice_cfg = {
            "provider": "kokoro",
            "voice": str(opts.get("voice") or "af_bella"),
            "lang_code": str(opts.get("lang_code") or "a"),
            "speed": float(opts.get("speed") or 1.0),
            "repo_id": (getattr(settings, "kokoro_hf_repo_id", None) or "hexgrad/Kokoro-82M").strip(),
        }
        usage_provider = "kokoro"
        usage_service = "narration_tts_kokoro_scene"
        usage_meta = {"chapter_id": str(ch.id), "scene_id": str(sc.id), **voice_cfg}
    elif provider == "chatterbox_turbo":
        ref_p = resolve_chatterbox_ref_to_path(str(opts.get("ref_path") or ""), storage_root=storage_root)
        mp3_bytes, dur = synthesize_chapter_narration_mp3_chatterbox(
            body,
            settings,
            variant="turbo",
            ref_audio_path=ref_p,
            language_id=None,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        voice_cfg = {"provider": "chatterbox_turbo", "ref_path": str(ref_p)}
        usage_provider = "chatterbox"
        usage_service = "narration_tts_chatterbox_turbo_scene"
        usage_meta = {"chapter_id": str(ch.id), "scene_id": str(sc.id), "ref_path": str(ref_p)}
    elif provider == "chatterbox_mtl":
        ref_p = resolve_chatterbox_ref_to_path(str(opts.get("ref_path") or ""), storage_root=storage_root)
        mp3_bytes, dur = synthesize_chapter_narration_mp3_chatterbox(
            body,
            settings,
            variant="mtl",
            ref_audio_path=ref_p,
            language_id=str(opts.get("language_id") or "en"),
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        voice_cfg = {
            "provider": "chatterbox_mtl",
            "ref_path": str(ref_p),
            "language_id": str(opts.get("language_id") or "en"),
        }
        usage_provider = "chatterbox"
        usage_service = "narration_tts_chatterbox_mtl_scene"
        usage_meta = {"chapter_id": str(ch.id), "scene_id": str(sc.id), **voice_cfg}
    elif provider == "elevenlabs":
        mp3_bytes, dur = synthesize_chapter_narration_mp3_elevenlabs(
            body,
            settings,
            voice_id=str(opts.get("voice_id") or ""),
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        model = (getattr(settings, "elevenlabs_model_id", None) or "eleven_multilingual_v2").strip()
        voice_cfg = {
            "provider": "elevenlabs",
            "model": model,
            "voice_id": str(opts.get("voice_id") or getattr(settings, "elevenlabs_voice_id", "") or ""),
        }
        usage_provider = "elevenlabs"
        usage_service = "narration_tts_elevenlabs_scene"
        usage_meta = {
            "chapter_id": str(ch.id),
            "scene_id": str(sc.id),
            "voice_id": voice_cfg["voice_id"],
            "model": model,
        }
    elif provider == "gemini":
        voice_g = str(opts.get("voice") or "Kore")
        mp3_bytes, dur = synthesize_chapter_narration_mp3_gemini(
            body,
            settings,
            voice_name=voice_g,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        model = (getattr(settings, "gemini_tts_model", None) or "gemini-2.5-flash-preview-tts").strip()
        voice_cfg = {"provider": "gemini", "model": model, "voice": voice_g}
        usage_provider = "gemini"
        usage_service = "narration_tts_gemini_scene"
        usage_meta = {"chapter_id": str(ch.id), "scene_id": str(sc.id), "voice": voice_g, "model": model}
    else:
        voice = str(opts.get("voice") or "alloy")
        mp3_bytes, dur = synthesize_chapter_narration_mp3(
            body,
            settings,
            voice=voice,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        model = (settings.openai_tts_model or "tts-1").strip() or "tts-1"
        voice_cfg = {"provider": "openai", "model": model, "voice": voice}
        usage_provider = "openai"
        usage_service = "narration_tts_openai_scene"
        usage_meta = {"chapter_id": str(ch.id), "scene_id": str(sc.id), "voice": voice, "model": model}

    if provider == "kokoro" and webvtt:
        storage.put_bytes(vtt_key, webvtt.encode("utf-8"), content_type="text/vtt")

    key = f"narrations/{project.id}/scenes/{sc.id}.mp3"
    audio_url = storage.put_bytes(key, mp3_bytes, content_type="audio/mpeg")

    for nt in db.scalars(select(NarrationTrack).where(NarrationTrack.scene_id == sc.id)).all():
        db.delete(nt)

    nt = NarrationTrack(
        id=uuid.uuid4(),
        tenant_id=tenant,
        project_id=project.id,
        chapter_id=ch.id,
        scene_id=sc.id,
        text=body,
        voice_config_json=voice_cfg,
        audio_url=audio_url,
        duration_sec=dur,
    )
    db.add(nt)
    bump_scene_planned_duration_for_narration(
        db,
        sc,
        float(dur),
        tail_padding_sec=scene_vo_tail_padding_sec_from_settings(settings),
    )
    char_n = float(max(0, len(body or "")))
    cr = compute_request_credits(
        provider=usage_provider,
        service_type=usage_service,
        unit_type="tts_chars",
        units=char_n,
        meta=usage_meta,
    )
    db.add(
        UsageRecord(
            id=uuid.uuid4(),
            tenant_id=tenant,
            project_id=project.id,
            scene_id=sc.id,
            asset_id=None,
            provider=usage_provider,
            service_type=usage_service,
            units=char_n,
            unit_type="tts_chars",
            cost_estimate=0.0,
            credits=cr,
            meta_json=usage_meta,
        )
    )
    return {"narration_track_id": str(nt.id), "duration_sec": dur, "scene_id": str(sc.id)}
