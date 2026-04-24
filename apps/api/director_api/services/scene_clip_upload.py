"""Validate and classify user-uploaded scene clips (image / video / audio)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
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


def video_file_has_audio_stream(path: Path, *, ffprobe_bin: str) -> bool:
    """Return True if ffprobe finds at least one audio stream."""
    fb = (ffprobe_bin or "ffprobe").strip() or "ffprobe"
    try:
        proc = subprocess.run(
            [
                fb,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    return bool((proc.stdout or "").strip())


def trim_video_file_to_max_seconds(
    src: Path,
    *,
    max_sec: float = MAX_CLIP_SECONDS,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 420.0,
) -> Path:
    """Re-encode the first ``max_sec`` seconds into a new temp MP4. Returns the new path; does not delete ``src``."""
    fb = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(fb):
        raise RuntimeError("ffmpeg not found on PATH (required to trim video to scene clip length)")
    has_audio = video_file_has_audio_stream(src, ffprobe_bin=ffprobe_bin)
    with NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        out = Path(tmp.name)
    cmd: list[str] = [
        fb,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-t",
        str(max_sec),
        "-map",
        "0:v:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if has_audio:
        cmd.extend(["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"])
    else:
        cmd.append("-an")
    cmd.append(str(out))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except FileNotFoundError as e:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg binary missing") from e
    except subprocess.TimeoutExpired as e:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg trim timed out") from e
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        tail = ((proc.stderr or "") + (proc.stdout or ""))[-2000:]
        raise RuntimeError(tail.strip() or "ffmpeg trim failed")
    if not out.is_file() or out.stat().st_size < 32:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg produced empty or tiny output")
    return out


def ffprobe_video_stream_dimensions(path: Path, *, ffprobe_bin: str) -> tuple[int, int] | None:
    """Return ``(width, height)`` of the first video stream, or ``None`` if unknown."""
    fb = (ffprobe_bin or "ffprobe").strip() or "ffprobe"
    try:
        proc = subprocess.run(
            [
                fb,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60.0,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip()
    if not line or "x" not in line:
        return None
    left, right = line.split("x", 1)
    try:
        w, h = int(left), int(right)
    except ValueError:
        return None
    if w > 0 and h > 0:
        return w, h
    return None


def _dims_match_target(w: int, h: int, tw: int, th: int, tol: int = 2) -> bool:
    return abs(w - tw) <= tol and abs(h - th) <= tol


def _vf_reframe_to_target(tw: int, th: int, *, frame_fit: str) -> str:
    """FFmpeg ``-vf`` chain: fill+center-crop vs letterbox (matches rough-cut scale+pad style)."""
    ft = (frame_fit or "").strip().lower()
    if ft == "letterbox":
        return (
            f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
            f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        )
    return f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}"


def reframe_still_image_center_crop(
    src: Path,
    *,
    target_w: int,
    target_h: int,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 180.0,
    frame_fit: str = "center_crop",
) -> Path:
    """Scale a still image to exact ``target_w``×``target_h`` (JPEG): center-crop or letterbox. Returns ``src`` if already matched."""
    tw, th = int(target_w), int(target_h)
    if tw < 16 or th < 16:
        raise RuntimeError("invalid target dimensions for reframing")
    fb = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(fb):
        raise RuntimeError("ffmpeg not found on PATH (required to fit Pexels stills to project frame)")
    cur = ffprobe_video_stream_dimensions(src, ffprobe_bin=ffprobe_bin)
    if cur is not None and _dims_match_target(cur[0], cur[1], tw, th):
        return src
    vf = _vf_reframe_to_target(tw, th, frame_fit=frame_fit)
    with NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        out = Path(tmp.name)
    cmd: list[str] = [
        fb,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except FileNotFoundError as e:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg binary missing") from e
    except subprocess.TimeoutExpired as e:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg image reframe timed out") from e
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        tail = ((proc.stderr or "") + (proc.stdout or ""))[-2000:]
        raise RuntimeError(tail.strip() or "ffmpeg image reframe failed")
    if not out.is_file() or out.stat().st_size < 32:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg produced empty or tiny image output")
    return out


def reframe_video_center_crop(
    src: Path,
    *,
    target_w: int,
    target_h: int,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 600.0,
    frame_fit: str = "center_crop",
) -> Path:
    """Re-encode video to MP4 to exact ``target_w``×``target_h`` (center-crop or letterbox). Returns ``src`` if dimensions already match."""
    tw, th = int(target_w), int(target_h)
    if tw < 16 or th < 16:
        raise RuntimeError("invalid target dimensions for reframing")
    fb = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(fb):
        raise RuntimeError("ffmpeg not found on PATH (required to fit Pexels video to project frame)")
    cur = ffprobe_video_stream_dimensions(src, ffprobe_bin=ffprobe_bin)
    if cur is not None and _dims_match_target(cur[0], cur[1], tw, th):
        return src
    has_audio = video_file_has_audio_stream(src, ffprobe_bin=ffprobe_bin)
    vf = _vf_reframe_to_target(tw, th, frame_fit=frame_fit)
    with NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        out = Path(tmp.name)
    cmd: list[str] = [
        fb,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vf",
        vf,
        "-map",
        "0:v:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if has_audio:
        cmd.extend(["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"])
    else:
        cmd.append("-an")
    cmd.append(str(out))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except FileNotFoundError as e:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg binary missing") from e
    except subprocess.TimeoutExpired as e:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg video reframe timed out") from e
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        tail = ((proc.stderr or "") + (proc.stdout or ""))[-2000:]
        raise RuntimeError(tail.strip() or "ffmpeg video reframe failed")
    if not out.is_file() or out.stat().st_size < 32:
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg produced empty or tiny video output")
    return out
