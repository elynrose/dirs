"""Validate and classify user-uploaded scene clips (image / video / audio)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

AssetKind = Literal["image", "video", "audio"]

# Pipeline layout matches worker: assets/<project_id>/<scene_id>/<asset_id>.<ext>
MAX_CLIP_SECONDS = 10.0
# Allow tiny muxing / float drift above the limit without rejecting borderline files.
_DURATION_SLACK_SEC = 0.06
MAX_UPLOAD_BYTES = 120 * 1024 * 1024

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".heic", ".tif", ".tiff"})
_VIDEO_EXTS = frozenset({".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi"})
_AUDIO_EXTS = frozenset({".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"})

AMBIGUOUS_EXTS = frozenset({".webm", ".mkv"})


def validate_explicit_clip_kind(*, kind_hint: str | None, filename: str | None) -> None:
    """Reject obvious mismatch when the user forces ``clip_kind`` (not ``auto``)."""
    if not kind_hint or kind_hint.strip().lower() == "auto":
        return
    h = kind_hint.strip().lower()
    ext = Path(filename or "").suffix.lower()
    if not ext:
        return
    if h == "image" and ext in _VIDEO_EXTS and ext not in AMBIGUOUS_EXTS:
        raise ValueError("clip_kind is image but the file extension looks like a video container")
    if h == "video" and ext in _IMAGE_EXTS and ext not in (".gif", ".webp", ".avif"):
        raise ValueError("clip_kind is video but the file extension looks like a still image")
    if h == "audio":
        if ext in _IMAGE_EXTS:
            raise ValueError("clip_kind is audio but the file extension looks like an image")
        # Allow .webm/.mkv for audio-only uploads; reject obvious video containers.
        if ext in _VIDEO_EXTS and ext not in AMBIGUOUS_EXTS:
            raise ValueError("clip_kind is audio but the file extension looks like a video container")


def normalized_extension(asset_kind: AssetKind, ext: str) -> str:
    e = (ext or "").lower()
    if not e.startswith("."):
        e = f".{e}"
    if asset_kind == "image":
        if e in (".jpeg", ".jpe"):
            return ".jpg"
        if e == ".tif":
            return ".tiff"
    return e if e != "." else ".bin"


def classify_from_filename_and_hint(
    filename: str | None,
    *,
    kind_hint: str | None,
) -> tuple[AssetKind, str]:
    """Return (asset_kind, extension). ``kind_hint`` is ``auto`` or a concrete kind."""
    ext = Path(filename or "upload.bin").suffix.lower() or ".bin"
    if not ext.startswith("."):
        ext = f".{ext}"

    h = (kind_hint or "auto").strip().lower()
    if h in ("image", "video", "audio"):
        return h, ext

    if ext in _IMAGE_EXTS:
        return "image", ext
    if ext in _VIDEO_EXTS and ext not in AMBIGUOUS_EXTS:
        return "video", ext
    if ext in _AUDIO_EXTS:
        return "audio", ext
    if ext in AMBIGUOUS_EXTS:
        return "video", ext  # refined later via ffprobe
    # Default: treat unknown as video attempt (common for misnamed files)
    return "video", ext if ext != ".bin" else ".mp4"


def refine_ambiguous_kind(path: Path, *, ffprobe_bin: str, initial: AssetKind) -> AssetKind:
    """For webm/mkv, choose video vs audio from stream types."""
    if initial != "video":
        return initial
    try:
        proc = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60.0,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return initial
    if proc.returncode != 0:
        return initial
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    lines = [ln.strip().lower() for ln in text.splitlines() if ln.strip()]
    if any("video" in ln for ln in lines):
        return "video"
    if any("audio" in ln for ln in lines):
        return "audio"
    return initial


def media_duration_seconds(path: Path, *, ffprobe_bin: str) -> float | None:
    """Positive duration in seconds, or ``None`` if not determined (e.g. still image)."""
    from ffmpeg_pipelines.probe import ffprobe_duration_seconds

    try:
        d = float(ffprobe_duration_seconds(path, ffprobe_bin=ffprobe_bin, timeout_sec=120.0))
    except (FileNotFoundError, OSError, RuntimeError, ValueError, TypeError):
        return None
    if d <= 0:
        return None
    return d


def assert_clip_duration_within_limit(
    path: Path,
    *,
    asset_kind: AssetKind,
    ffprobe_bin: str,
) -> float | None:
    """Enforce MAX_CLIP_SECONDS for video and audio. Returns measured duration when applicable."""
    if asset_kind == "image":
        d = media_duration_seconds(path, ffprobe_bin=ffprobe_bin)
        if d is None:
            return None
        # Animated still formats (GIF, some WEBP)
        if d > MAX_CLIP_SECONDS + _DURATION_SLACK_SEC:
            raise ValueError(f"clip too long: {d:.2f}s (max {MAX_CLIP_SECONDS:g}s)")
        return d

    d = media_duration_seconds(path, ffprobe_bin=ffprobe_bin)
    if d is None:
        raise ValueError("could not read media duration (try another format)")
    if d > MAX_CLIP_SECONDS + _DURATION_SLACK_SEC:
        raise ValueError(f"clip too long: {d:.2f}s (max {MAX_CLIP_SECONDS:g}s)")
    return d
