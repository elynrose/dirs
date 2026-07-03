"""Random Ken Burns (zoom/pan) for still-image scenes, with a selectable renderer.

Two engines, chosen via the ``still_motion_renderer`` setting:

* ``"cpu"`` — FFmpeg ``zoompan`` (the existing, dependency-free path).
* ``"gpu"`` — a PyTorch/CUDA sidecar that warps each frame on the GPU and pipes raw frames
  into ``h264_nvenc``. Runs out-of-process because the worker runtime (Python 3.14) has no
  CUDA PyTorch; the sidecar uses its own Python 3.11 + torch venv.

Motion is chosen deterministically from the asset id, so re-compiles and the precompile cache
stay stable. If the GPU engine is unavailable or fails, we fall back to the CPU engine so an
export never breaks over an optional effect.
"""

from __future__ import annotations

import hashlib
import random
import subprocess
from pathlib import Path
from typing import Any, Literal

import structlog

from ffmpeg_pipelines.paths import ffmpeg_argv_path, path_is_readable_file, path_stat
from ffmpeg_pipelines.still_to_video import encode_image_to_mp4

log = structlog.get_logger(__name__)

MotionMode = Literal["none", "pan", "zoom"]
Direction = Literal["in", "out", "left", "right"]

_VALID_RENDERERS = ("off", "cpu", "gpu")


def resolve_still_motion_renderer(settings: Any) -> str:
    v = str(getattr(settings, "still_motion_renderer", "off") or "off").strip().lower()
    return v if v in _VALID_RENDERERS else "off"


def still_motion_enabled(settings: Any) -> bool:
    return resolve_still_motion_renderer(settings) != "off"


def motion_signature(settings: Any) -> str:
    """Cache key fragment so precompiled stills invalidate when the renderer changes."""
    return f"sm:{resolve_still_motion_renderer(settings)}"


def _seed_for_asset(asset_id: Any) -> int:
    return int(hashlib.sha256(str(asset_id).encode("utf-8")).hexdigest()[:8], 16)


def pick_motion_for_asset(asset_id: Any, settings: Any) -> tuple[MotionMode, Direction]:
    """Deterministically choose (motion, direction) for a still from its asset id."""
    if not still_motion_enabled(settings):
        return ("none", "in")
    rnd = random.Random(_seed_for_asset(asset_id))
    choice = rnd.choice(("zoom_in", "zoom_out", "pan_left", "pan_right"))
    if choice == "zoom_in":
        return ("zoom", "in")
    if choice == "zoom_out":
        return ("zoom", "out")
    if choice == "pan_left":
        return ("pan", "left")
    return ("pan", "right")


def _gpu_python(settings: Any) -> str | None:
    p = str(getattr(settings, "gpu_still_motion_python", "") or "").strip()
    if not p:
        return None
    return p if Path(p).is_file() else None


def gpu_sidecar_script_path() -> Path:
    """Path to the standalone CUDA renderer (never imported by the worker)."""
    import director_api

    return Path(director_api.__file__).resolve().parent / "gpu" / "ken_burns_render.py"


def _render_gpu(
    image_path: Path,
    output_path: Path,
    *,
    duration_sec: float,
    width: int,
    height: int,
    fps: int,
    motion: MotionMode,
    direction: Direction,
    ffmpeg_bin: str,
    nvenc_encoder: str,
    cq: int,
    gpu_python: str,
    timeout_sec: float,
) -> bool:
    """Run the CUDA sidecar. Returns True on a valid output, False to signal CPU fallback."""
    script = gpu_sidecar_script_path()
    if not script.is_file():
        log.warning("gpu_still_motion_sidecar_missing", script=str(script))
        return False
    cmd = [
        gpu_python,
        str(script),
        "--image", ffmpeg_argv_path(image_path),
        "--out", ffmpeg_argv_path(output_path),
        "--duration", f"{float(duration_sec):.3f}",
        "--width", str(int(width)),
        "--height", str(int(height)),
        "--fps", str(int(fps)),
        "--motion", motion,
        "--direction", direction,
        "--ffmpeg", ffmpeg_bin,
        "--nvenc", nvenc_encoder,
        "--cq", str(int(cq)),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("gpu_still_motion_sidecar_error", error=str(e)[:500])
        return False
    if proc.returncode != 0:
        log.warning(
            "gpu_still_motion_sidecar_failed",
            returncode=proc.returncode,
            stderr=(proc.stderr or proc.stdout or "")[-800:],
        )
        return False
    if not path_is_readable_file(output_path) or path_stat(output_path).st_size < 32:
        log.warning("gpu_still_motion_sidecar_empty_output")
        return False
    return True


def render_still_motion_mp4(
    image_path: Path,
    output_path: Path,
    *,
    duration_sec: float,
    width: int,
    height: int,
    fps: int = 30,
    settings: Any,
    ffmpeg_bin: str = "ffmpeg",
    timeout_sec: float = 900.0,
    motion: MotionMode | None = None,
    direction: Direction | None = None,
    asset_id: Any | None = None,
) -> dict[str, Any]:
    """Encode a still to MP4 with random Ken Burns, dispatching to the configured renderer.

    ``motion``/``direction`` may be provided directly; otherwise they are derived from ``asset_id``.
    GPU failures fall back to the CPU (zoompan) encoder so exports never break.
    """
    renderer = resolve_still_motion_renderer(settings)
    if motion is None or direction is None:
        motion, direction = pick_motion_for_asset(asset_id, settings) if asset_id is not None else ("none", "in")

    if renderer == "gpu" and motion != "none":
        gpu_py = _gpu_python(settings)
        if gpu_py is not None:
            from ffmpeg_pipelines.encode import resolve_video_encoder

            nvenc = resolve_video_encoder(ffmpeg_bin, "h264_nvenc")
            if nvenc == "h264_nvenc":
                ok = _render_gpu(
                    image_path,
                    output_path,
                    duration_sec=duration_sec,
                    width=width,
                    height=height,
                    fps=fps,
                    motion=motion,
                    direction=direction,
                    ffmpeg_bin=ffmpeg_bin,
                    nvenc_encoder=nvenc,
                    cq=23,
                    gpu_python=gpu_py,
                    timeout_sec=min(float(timeout_sec), 1800.0),
                )
                if ok:
                    return {
                        "output_path": str(output_path),
                        "bytes": path_stat(output_path).st_size,
                        "duration_sec": float(duration_sec),
                        "mode": "still_motion_gpu",
                        "motion": motion,
                        "direction": direction,
                        "renderer": "gpu",
                    }
                log.info("gpu_still_motion_fallback_cpu", asset_id=str(asset_id) if asset_id else None)
        else:
            log.info("gpu_still_motion_python_unconfigured_fallback_cpu")

    # CPU (zoompan) path — also the fallback when GPU is off/unavailable/failed.
    kb_dir = direction if direction in ("in", "out") else "in"
    meta = encode_image_to_mp4(
        image_path,
        output_path,
        duration_sec=float(duration_sec),
        width=width,
        height=height,
        fps=fps,
        ffmpeg_bin=ffmpeg_bin,
        timeout_sec=timeout_sec,
        motion=motion,
        ken_burns_direction=kb_dir,
    )
    meta["renderer"] = "cpu" if motion != "none" else "off"
    meta["direction"] = direction
    return meta
