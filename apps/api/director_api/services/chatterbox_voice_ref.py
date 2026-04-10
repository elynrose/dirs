"""Workspace Chatterbox reference clip: path under ``LOCAL_STORAGE_ROOT`` + ffmpeg normalize."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def safe_tenant_slug(tenant_id: str) -> str:
    t = (tenant_id or "").strip()
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", t).strip("_")
    return (s[:128] if s else "default") or "default"


def voice_ref_storage_key(tenant_id: str) -> str:
    return f"voice_refs/{safe_tenant_slug(tenant_id)}/reference.wav"


def voice_ref_absolute_path(*, storage_root: Path, tenant_id: str) -> Path:
    key = voice_ref_storage_key(tenant_id)
    return (storage_root / key).resolve()


def convert_upload_to_reference_wav(
    *,
    src_path: Path,
    dest_wav: Path,
    ffmpeg_bin: str,
    timeout_sec: float = 120.0,
) -> None:
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(src_path),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(dest_wav),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if proc.returncode != 0 or not dest_wav.is_file():
        tail = (proc.stderr or proc.stdout or "")[-4000:]
        raise RuntimeError(tail.strip() or "ffmpeg reference conversion failed")
