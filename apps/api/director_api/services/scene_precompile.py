"""Background per-asset video precompile for faster rough/final cuts."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import structlog

from director_api.config import Settings
from director_api.db.models import Asset
from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file, path_stat
from ffmpeg_pipelines.probe import ffprobe_duration_seconds
from ffmpeg_pipelines.still_to_video import encode_image_to_mp4
from ffmpeg_pipelines.video_to_duration import encode_video_to_target_duration_mp4

log = structlog.get_logger(__name__)

PRECOMPILE_DIR_NAME = "precompiled"


def precompile_project_dir(storage_root: Path, project_id: uuid.UUID) -> Path:
    return storage_root / PRECOMPILE_DIR_NAME / str(project_id)


def precompile_mp4_path(storage_root: Path, project_id: uuid.UUID, asset_id: uuid.UUID) -> Path:
    return precompile_project_dir(storage_root, project_id) / f"{asset_id}.mp4"


def precompile_meta_path(storage_root: Path, project_id: uuid.UUID, asset_id: uuid.UUID) -> Path:
    return precompile_project_dir(storage_root, project_id) / f"{asset_id}.meta.json"


def precompile_storage_fingerprint(m: dict[str, Any]) -> str:
    """Match precompile cache to source media; clip duration is handled via trim at concat."""
    parts = [
        str(m.get("asset_id") or ""),
        str(m.get("storage_url") or ""),
        str(m.get("asset_type") or "").lower(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def precompile_storage_fingerprint_for_asset(asset: Asset) -> str:
    return precompile_storage_fingerprint(
        {
            "asset_id": str(asset.id),
            "storage_url": str(asset.storage_url or ""),
            "asset_type": str(asset.asset_type or "").lower(),
        }
    )


def manifest_row_fingerprint(m: dict[str, Any]) -> str:
    """Stable key for whether a precompiled segment still matches export intent."""
    parts = [
        str(m.get("asset_id") or ""),
        str(m.get("storage_url") or ""),
        str(m.get("asset_type") or "").lower(),
        str(m.get("duration_sec") if m.get("duration_sec") is not None else ""),
        str(m.get("trim_start_sec") if m.get("trim_start_sec") is not None else ""),
        str(m.get("trim_end_sec") if m.get("trim_end_sec") is not None else ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def asset_source_fingerprint(asset: Asset, *, duration_sec: float) -> str:
    """Legacy full fingerprint including duration (export manifest versioning)."""
    parts = [
        str(asset.id),
        str(asset.scene_id or ""),
        str(asset.asset_type or "").lower(),
        str(asset.storage_url or ""),
        f"{float(duration_sec):.3f}",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _meta_storage_fingerprint(meta: dict[str, Any]) -> str:
    stored = str(meta.get("storage_fingerprint") or "").strip()
    if stored:
        return stored
    return str(meta.get("fingerprint") or "")


def _duration_sec_value(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def precompile_is_current(
    *,
    storage_root: Path,
    project_id: uuid.UUID,
    asset_id: uuid.UUID,
    fingerprint: str,
    clip_duration_sec: float | None = None,
    motion_sig: str | None = None,
) -> bool:
    mp4 = precompile_mp4_path(storage_root, project_id, asset_id)
    meta = read_precompile_meta(precompile_meta_path(storage_root, project_id, asset_id))
    if not path_is_readable_file(mp4) or meta is None:
        return False
    if _meta_storage_fingerprint(meta) != fingerprint:
        return False
    if motion_sig is not None and str(meta.get("motion_sig") or "") != motion_sig:
        return False
    pre_dur = _duration_sec_value(meta.get("duration_sec"))
    clip_dur = clip_duration_sec
    if clip_dur is not None and pre_dur is not None and clip_dur > pre_dur + 0.05:
        return False
    try:
        return path_stat(mp4).st_size >= 32
    except OSError:
        return False


def default_duration_sec_for_asset(
    asset: Asset,
    settings: Settings,
    *,
    storage_root: Path,
    ffprobe_bin: str = "ffprobe",
) -> float:
    if asset.asset_type == "image":
        return float(getattr(settings, "scene_clip_duration_sec", 10) or 10)
    lp = path_from_storage_url(asset.storage_url, storage_root=storage_root)
    if lp is None or not path_is_readable_file(lp):
        return float(getattr(settings, "scene_clip_duration_sec", 10) or 10)
    try:
        native = float(
            ffprobe_duration_seconds(lp, ffprobe_bin=ffprobe_bin, timeout_sec=120.0)
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError, TypeError):
        native = 0.0
    cap = float(getattr(settings, "scene_clip_duration_sec", 10) or 10)
    if native > 0:
        return min(native, cap) if cap > 0 else native
    return cap


def read_precompile_meta(meta_path: Path) -> dict[str, Any] | None:
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def resolve_precompiled_video_path(
    *,
    storage_root: Path,
    project_id: uuid.UUID,
    manifest_row: dict[str, Any],
    motion_sig: str | None = None,
) -> Path | None:
    aid = uuid.UUID(str(manifest_row["asset_id"]))
    fp = precompile_storage_fingerprint(manifest_row)
    clip_dur = _duration_sec_value(manifest_row.get("duration_sec"))
    if not precompile_is_current(
        storage_root=storage_root,
        project_id=project_id,
        asset_id=aid,
        fingerprint=fp,
        clip_duration_sec=clip_dur,
        motion_sig=motion_sig,
    ):
        return None
    p = precompile_mp4_path(storage_root, project_id, aid)
    return p if path_is_readable_file(p) else None


def compile_asset_precompile(
    *,
    storage_root: Path,
    project_id: uuid.UUID,
    asset: Asset,
    duration_sec: float,
    width: int,
    height: int,
    settings: Settings,
) -> Path:
    if asset.asset_type not in ("image", "video"):
        raise ValueError(f"unsupported asset_type for precompile: {asset.asset_type}")
    lp = path_from_storage_url(asset.storage_url, storage_root=storage_root)
    if lp is None or not path_is_readable_file(lp):
        raise FileNotFoundError(f"asset file missing: {asset.id}")

    out_dir = precompile_project_dir(storage_root, project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = precompile_mp4_path(storage_root, project_id, asset.id)
    tmp = out_mp4.with_suffix(".tmp.mp4")
    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (getattr(settings, "ffprobe_bin", None) or "ffprobe").strip() or "ffprobe"
    timeout = float(getattr(settings, "ffmpeg_timeout_sec", 3600.0) or 3600.0)
    dur = max(0.5, min(float(duration_sec), 7200.0))

    if asset.asset_type == "image":
        from director_api.services.still_motion import render_still_motion_mp4

        render_still_motion_mp4(
            lp,
            tmp,
            duration_sec=dur,
            width=width,
            height=height,
            settings=settings,
            ffmpeg_bin=ffmpeg_bin,
            timeout_sec=min(timeout, 7200.0),
            asset_id=asset.id,
        )
    else:
        encode_video_to_target_duration_mp4(
            lp,
            tmp,
            target_sec=dur,
            width=width,
            height=height,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=min(timeout, 7200.0),
        )

    if not path_is_readable_file(tmp) or path_stat(tmp).st_size < 32:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("precompile produced empty output")

    if out_mp4.exists():
        out_mp4.unlink()
    tmp.replace(out_mp4)
    return out_mp4


def write_precompile_meta(
    *,
    storage_root: Path,
    project_id: uuid.UUID,
    asset: Asset,
    fingerprint: str,
    duration_sec: float,
    width: int,
    height: int,
    motion_sig: str = "",
) -> None:
    meta_path = precompile_meta_path(storage_root, project_id, asset.id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "asset_id": str(asset.id),
        "scene_id": str(asset.scene_id) if asset.scene_id else None,
        "project_id": str(project_id),
        "storage_fingerprint": fingerprint,
        "fingerprint": fingerprint,
        "duration_sec": float(duration_sec),
        "width": int(width),
        "height": int(height),
        "asset_type": str(asset.asset_type),
        "motion_sig": str(motion_sig or ""),
    }
    meta_path.write_text(json.dumps(payload, indent=0), encoding="utf-8")


def invalidate_precompiles_for_scene(
    storage_root: Path,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    *,
    keep_asset_id: uuid.UUID | None = None,
) -> int:
    """Remove precompiled segments for a scene (e.g. when a new take replaces an old one)."""
    root = precompile_project_dir(storage_root, project_id)
    if not root.is_dir():
        return 0
    removed = 0
    sid = str(scene_id)
    for meta_path in root.glob("*.meta.json"):
        meta = read_precompile_meta(meta_path)
        if not meta or str(meta.get("scene_id") or "") != sid:
            continue
        aid_raw = meta.get("asset_id")
        if keep_asset_id is not None and str(keep_asset_id) == str(aid_raw):
            continue
        try:
            aid = uuid.UUID(str(aid_raw))
        except (ValueError, TypeError):
            aid = None
        meta_path.unlink(missing_ok=True)
        if aid is not None:
            precompile_mp4_path(storage_root, project_id, aid).unlink(missing_ok=True)
        removed += 1
    return removed


def delete_project_precompiles(storage_root: Path, project_id: uuid.UUID) -> int:
    root = precompile_project_dir(storage_root, project_id)
    if not root.is_dir():
        return 0
    count = sum(1 for _ in root.iterdir())
    shutil.rmtree(root, ignore_errors=True)
    log.info("scene_precompile_project_deleted", project_id=str(project_id), files=count)
    return count


def substitute_precompiled_clip_segments(
    segments: list[Any],
    manifest: list[dict[str, Any]],
    *,
    storage_root: Path,
    project_id: uuid.UUID,
    motion_sig: str | None = None,
) -> tuple[list[Any], int]:
    """
    Replace image/video segments with precompiled MP4 paths when fingerprints match.

  Returns (new_segments, substituted_count).
    """
    manifest_iter = iter(manifest)
    out: list[Any] = []
    substituted = 0
    for seg in segments:
        if not isinstance(seg, tuple) or not seg:
            out.append(seg)
            continue
        kind = seg[0]
        if kind == "chapter_title":
            out.append(seg)
            continue
        if kind not in ("image", "video"):
            out.append(seg)
            continue
        m = next(manifest_iter, None)
        if m is None:
            out.append(seg)
            continue
        pre = resolve_precompiled_video_path(
            storage_root=storage_root,
            project_id=project_id,
            manifest_row=m,
            motion_sig=motion_sig if kind == "image" else None,
        )
        if pre is None:
            out.append(seg)
            continue
        clip_dur = _duration_sec_value(m.get("duration_sec"))
        meta = read_precompile_meta(precompile_meta_path(storage_root, project_id, uuid.UUID(str(m["asset_id"]))))
        pre_dur = _duration_sec_value(meta.get("duration_sec") if meta else None)
        if (
            clip_dur is not None
            and pre_dur is not None
            and abs(clip_dur - pre_dur) > 0.05
        ):
            out.append(("video", pre, clip_dur))
        else:
            out.append(("video", pre, None))
        substituted += 1
    return out, substituted
