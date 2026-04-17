"""Save browser-recorded (microphone) audio as the scene VO ``NarrationTrack`` (replaces TTS)."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from sqlalchemy import select

from director_api.db.models import NarrationTrack, Scene
from director_api.services.research_service import sanitize_jsonb_text
from director_api.services.scene_timeline_duration import (
    bump_scene_planned_duration_for_narration,
    scene_vo_tail_padding_sec_from_settings,
)
from director_api.services.scene_clip_upload import media_duration_seconds
from director_api.storage.filesystem import FilesystemStorage
from ffmpeg_pipelines.paths import ffmpeg_argv_path, path_is_readable_file

_SCENE_VO_MIC_MAX_BYTES = 40 * 1024 * 1024
_SCENE_VO_MIC_MAX_SEC = 600.0


def _transcode_to_mp3(src: Path, dst: Path, *, ffmpeg_bin: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        ffmpeg_argv_path(src),
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        ffmpeg_argv_path(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "ffmpeg failed")[:2000]
        raise ValueError(tail)


def save_scene_narration_from_microphone_upload(
    db: Any,
    *,
    scene: Scene,
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
    tenant_id: str,
    raw_bytes: bytes,
    original_filename: str,
    settings: Any,
) -> dict[str, Any]:
    """Store uploaded/recorded audio as canonical scene VO mp3; replaces any existing scene ``NarrationTrack``."""
    if len(raw_bytes) < 32:
        raise ValueError("uploaded audio too small")
    if len(raw_bytes) > _SCENE_VO_MIC_MAX_BYTES:
        raise ValueError(f"upload exceeds {_SCENE_VO_MIC_MAX_BYTES // (1024 * 1024)} MB")

    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (settings.ffprobe_bin or "ffprobe").strip() or "ffprobe"
    if not shutil.which(ffmpeg_bin):
        raise ValueError("ffmpeg not found on server PATH (required to encode scene VO)")

    ext = Path(original_filename or "recording.webm").suffix.lower()
    if ext not in (".webm", ".wav", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".flac"):
        ext = ".webm"

    storage = FilesystemStorage(settings.local_storage_root)

    tmp_in: Path | None = None
    tmp_mp3: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(raw_bytes)
            tmp_in = Path(tmp.name)

        dur_probe = media_duration_seconds(tmp_in, ffprobe_bin=ffprobe_bin)
        if dur_probe is None or dur_probe <= 0:
            raise ValueError("could not read audio duration (try WAV or MP3)")
        if dur_probe > _SCENE_VO_MIC_MAX_SEC + 0.5:
            raise ValueError(f"recording too long: {dur_probe:.1f}s (max {_SCENE_VO_MIC_MAX_SEC:.0f}s)")

        tf_out = NamedTemporaryFile(delete=False, suffix=".mp3")
        tf_out.close()
        tmp_mp3 = Path(tf_out.name)
        _transcode_to_mp3(tmp_in, tmp_mp3, ffmpeg_bin=ffmpeg_bin)

        dur_final = media_duration_seconds(tmp_mp3, ffprobe_bin=ffprobe_bin) or dur_probe
        if dur_final > _SCENE_VO_MIC_MAX_SEC + 0.5:
            raise ValueError(f"encoded audio too long: {dur_final:.1f}s")

        mp3_bytes = tmp_mp3.read_bytes()
        key = f"narrations/{project_id}/scenes/{scene.id}.mp3"
        audio_url = storage.put_bytes(key, mp3_bytes, content_type="audio/mpeg")

        vtt_key = f"narrations/{project_id}/scenes/{scene.id}.vtt"
        vtt_disk = storage.get_path(vtt_key)
        try:
            if path_is_readable_file(vtt_disk):
                vtt_disk.unlink()
        except OSError:
            pass

        for nt in db.scalars(select(NarrationTrack).where(NarrationTrack.scene_id == scene.id)).all():
            db.delete(nt)

        body = sanitize_jsonb_text((scene.narration_text or "").strip(), 12_000)
        if len(body) < 1:
            body = "."

        nt = NarrationTrack(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            chapter_id=chapter_id,
            scene_id=scene.id,
            text=body,
            voice_config_json={
                "provider": "microphone",
                "source": "upload",
                "original_name": (original_filename or "")[:240],
            },
            audio_url=audio_url,
            duration_sec=float(dur_final),
        )
        db.add(nt)
        bump_scene_planned_duration_for_narration(
            db,
            scene,
            float(dur_final),
            tail_padding_sec=scene_vo_tail_padding_sec_from_settings(settings),
        )
        db.commit()
        db.refresh(nt)
        return {
            "narration_track_id": str(nt.id),
            "duration_sec": float(dur_final),
            "scene_id": str(scene.id),
            "audio_url": audio_url,
        }
    finally:
        for p in (tmp_in, tmp_mp3):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
