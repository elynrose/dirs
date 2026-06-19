"""Media byte normalization helpers shared by worker modules."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import structlog

from director_api.db.models import Project
from director_api.services import phase3 as phase3_svc
from director_api.services.research_service import sanitize_jsonb_text

log = structlog.get_logger(__name__)


def _image_bytes_magic_ok(data: bytes) -> bool:
    """Best-effort image signature check for pass-through bytes."""
    if not data or len(data) < 4:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:2] == b"\xff\xd8":
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return True
    if data[:2] == b"BM" and len(data) >= 14:
        return True
    if len(data) >= 12 and data[4:8] == b"jP  ":
        return True
    window = data[: min(512, len(data))]
    if b"ftyp" in window:
        i = window.find(b"ftyp")
        if i >= 0 and i + 12 <= len(data):
            brands = data[i : i + 32]
            if b"avif" in brands or b"avis" in brands or b"mif1" in brands or b"msf1" in brands or b"heic" in brands:
                return True
    return False


def _project_export_dimensions(project: Project) -> tuple[int, int]:
    """Width x height for normalize, local still→video, and rough/final timeline compiles."""
    from director_api.services.project_frame import coerce_frame_aspect_ratio, frame_pixel_size

    return frame_pixel_size(coerce_frame_aspect_ratio(getattr(project, "frame_aspect_ratio", None)))


def _normalize_image_bytes_to_dims(
    settings: Any,
    data: bytes,
    content_type: str | None,
    target_w: int,
    target_h: int,
) -> tuple[bytes, str, bool]:
    """Crop/scale to target_w×target_h via ffmpeg."""
    ffmpeg_bin = (getattr(settings, "ffmpeg_bin", None) or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        return data, (content_type or "image/jpeg"), False
    in_suffix = ".jpg"
    ct = (content_type or "").lower()
    if "png" in ct:
        in_suffix = ".png"
    elif "webp" in ct:
        in_suffix = ".webp"
    elif "avif" in ct or "heif" in ct or "heic" in ct:
        in_suffix = ".avif"
    with tempfile.NamedTemporaryFile(suffix=in_suffix, delete=False) as fin:
        fin.write(data)
        in_path = Path(fin.name)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fout:
        out_path = Path(fout.name)
    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(in_path),
            "-vf",
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}",
            "-frames:v",
            "1",
            "-c:v",
            "mjpeg",
            "-q:v",
            "2",
            str(out_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=120)
        out_b = out_path.read_bytes()
        if len(out_b) >= 32:
            return out_b, "image/jpeg", True
        log.warning("ffmpeg_normalize_empty_or_tiny_output", out_len=len(out_b))
        return data, (content_type or "image/jpeg"), False
    except Exception as e:
        log.warning("ffmpeg_normalize_failed", error=str(e)[:300])
        return data, (content_type or "image/jpeg"), False
    finally:
        in_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def _normalize_video_bytes_to_dims(settings: Any, data: bytes, target_w: int, target_h: int) -> bytes:
    ffmpeg_bin = (getattr(settings, "ffmpeg_bin", None) or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        return data
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fin:
        fin.write(data)
        in_path = Path(fin.name)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fout:
        out_path = Path(fout.name)
    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(in_path),
            "-vf",
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(out_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=240)
        return out_path.read_bytes()
    except Exception:
        return data
    finally:
        in_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def _package_negative_prompt(pp: Any, *, project: Project | None = None, settings: Any | None = None) -> str | None:
    if project is not None and settings is not None:
        neg = phase3_svc.effective_scene_negative_prompt(
            project, settings, pp if isinstance(pp, dict) else None
        )
        return sanitize_jsonb_text(neg, 1200) if neg else None
    if not isinstance(pp, dict):
        return None
    n = pp.get("negative_prompt")
    if not isinstance(n, str) or not n.strip():
        return None
    return sanitize_jsonb_text(n.strip(), 1200)
