"""Structured export manifest (see docs/ffmpeg-baseline.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ffmpeg_pipelines.version_probe import ffmpeg_version_line


def build_export_manifest(
    *,
    output_url: str | None,
    inputs: list[dict[str, Any]],
    compile_meta: dict[str, Any],
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Return a JSON-serializable manifest for storage next to the export."""
    ver = ffmpeg_version_line(ffmpeg_bin) or "unknown"
    out_path = output_url
    if out_path and out_path.startswith("file://"):
        out_path = str(Path(out_path.replace("file://", "", 1)).resolve())
    return {
        "ffmpeg_pipeline_version": "0.1.0",
        "ffmpeg_version": ver,
        "encode_preset": "h264_delivery_720p",
        "video": {
            "codec": "libx264",
            "preset": str(compile_meta.get("preset") or "veryfast"),
            "crf": compile_meta.get("crf", 23),
            "pix_fmt": "yuv420p",
        },
        "audio": {
            "codec": "none",
            "note": "rough_cut stub — narration/music amix in a later slice",
        },
        "inputs": inputs,
        "output": {"url": output_url, "path": out_path},
        "compile": compile_meta,
    }
