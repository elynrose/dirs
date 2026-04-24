"""Execute Storyblocks scene import (GraphicStock stills + VideoBlocks footage)."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.api.schemas.phase3 import ImportStoryblocksBody
from director_api.config import Settings
from director_api.db.models import Asset, Chapter, Project, Scene
from director_api.providers.pexels_client import download_bytes_capped
from director_api.providers.storyblocks_client import (
    DEFAULT_STORYBLOCKS_IMAGE_API_BASE,
    DEFAULT_STORYBLOCKS_VIDEO_API_BASE,
    fetch_signed_download_url,
    fetch_stock_item_json,
    storyblocks_attribution_block,
)
from director_api.services.pexels_import_support import (
    PEXELS_TRIM_DURATION_SLACK_SEC,
    PEXELS_TRIM_MIN_SEC,
    resolve_pexels_trim_max_sec,
)
from director_api.services.project_frame import coerce_clip_frame_fit, coerce_frame_aspect_ratio, frame_pixel_size
from director_api.services.scene_clip_upload import (
    MAX_CLIP_SECONDS,
    MAX_UPLOAD_BYTES,
    assert_clip_duration_within_limit,
    classify_from_filename_and_hint,
    media_duration_seconds,
    normalized_extension,
    refine_ambiguous_kind,
    reframe_still_image_center_crop,
    reframe_video_center_crop,
    trim_video_file_to_max_seconds,
    AMBIGUOUS_EXTS,
)
from director_api.storage.filesystem import FilesystemStorage

log = structlog.get_logger(__name__)


class StoryblocksImportError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _storyblocks_credentials(settings: Settings) -> tuple[str, str, str, str]:
    pub = (getattr(settings, "storyblocks_public_key", None) or "").strip()
    priv = (getattr(settings, "storyblocks_private_key", None) or "").strip()
    vbase = (getattr(settings, "storyblocks_video_api_base", None) or "").strip() or DEFAULT_STORYBLOCKS_VIDEO_API_BASE
    ibase = (getattr(settings, "storyblocks_image_api_base", None) or "").strip() or DEFAULT_STORYBLOCKS_IMAGE_API_BASE
    return pub, priv, vbase.rstrip("/"), ibase.rstrip("/")


def _downloader_id_for_scene(scene_id: UUID) -> int:
    h = hashlib.sha256(str(scene_id).encode("utf-8")).hexdigest()
    return int(h[:9], 16) % 900_000_000 + 100


def _load_scene_for_storyblocks(db: Session, settings: Settings, scene_id: UUID) -> tuple[Scene, UUID]:
    sc = db.get(Scene, scene_id)
    if not sc:
        raise StoryblocksImportError(404, "NOT_FOUND", "scene not found")
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise StoryblocksImportError(404, "NOT_FOUND", "scene not found")
    p = db.get(Project, ch.project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise StoryblocksImportError(404, "NOT_FOUND", "scene not found")
    return sc, ch.project_id


async def execute_storyblocks_scene_import(
    db: Session,
    settings: Settings,
    scene_id: UUID,
    body: ImportStoryblocksBody,
) -> Asset:
    pub, priv, video_base, image_base = _storyblocks_credentials(settings)
    if not pub or not priv:
        raise StoryblocksImportError(
            503,
            "STORYBLOCKS_NOT_CONFIGURED",
            "Set Storyblocks API public and private keys (workspace Settings or environment) to import Storyblocks media.",
        )

    sc, project_id = _load_scene_for_storyblocks(db, settings, scene_id)
    api_base = image_base if body.kind == "photo" else video_base
    downloader_id = _downloader_id_for_scene(scene_id)

    title: str | None = None
    details_url: str | None = None
    try:
        item = await fetch_stock_item_json(
            public_key=pub,
            private_key=priv,
            base_url=api_base,
            stock_item_id=body.storyblocks_id,
        )
        if isinstance(item.get("title"), str):
            title = item["title"]
        if isinstance(item.get("details_url"), str):
            details_url = item["details_url"]
    except (httpx.HTTPError, ValueError) as e:
        log.warning("storyblocks_stock_item_optional_failed", error=str(e)[:240])

    orig_name = (
        f"storyblocks-photo-{body.storyblocks_id}.jpg"
        if body.kind == "photo"
        else f"storyblocks-video-{body.storyblocks_id}.mp4"
    )

    try:
        dl_url = await fetch_signed_download_url(
            public_key=pub,
            private_key=priv,
            base_url=api_base,
            stock_item_id=body.storyblocks_id,
            downloader_id=downloader_id,
        )
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            raise StoryblocksImportError(
                404, "STORYBLOCKS_NOT_FOUND", "That Storyblocks id was not found or is not downloadable."
            ) from e
        log.warning("storyblocks_download_meta_http", status_code=e.response.status_code if e.response else None)
        raise StoryblocksImportError(
            502, "STORYBLOCKS_UPSTREAM", "Storyblocks download request failed."
        ) from e
    except httpx.RequestError as e:
        raise StoryblocksImportError(502, "STORYBLOCKS_NETWORK", str(e)[:240]) from e
    except ValueError as e:
        raise StoryblocksImportError(422, "STORYBLOCKS_INVALID_RESPONSE", str(e)) from e

    try:
        raw, resp_ct = await download_bytes_capped(dl_url, max_bytes=MAX_UPLOAD_BYTES)
    except ValueError as e:
        msg = str(e)
        code = "TOO_LARGE" if "exceeds" in msg else "DOWNLOAD_ERROR"
        raise StoryblocksImportError(
            413 if code == "TOO_LARGE" else 422,
            code,
            msg,
        ) from e
    except httpx.HTTPStatusError as e:
        raise StoryblocksImportError(
            502, "STORYBLOCKS_DOWNLOAD", "Could not download media from Storyblocks CDN."
        ) from e
    except httpx.RequestError as e:
        raise StoryblocksImportError(502, "STORYBLOCKS_NETWORK", str(e)[:240]) from e

    if len(raw) < 16:
        raise StoryblocksImportError(422, "EMPTY", "downloaded file too small")

    hint = "image" if body.kind == "photo" else "video"
    asset_kind, ext_guess = classify_from_filename_and_hint(orig_name, kind_hint=hint)
    suffix = ext_guess if ext_guess.startswith(".") else f".{ext_guess}"

    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        ffprobe_bin = (settings.ffprobe_bin or "ffprobe").strip() or "ffprobe"
        ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
        trim_meta: dict[str, object] = {}
        if body.kind == "video":
            d0 = media_duration_seconds(tmp_path, ffprobe_bin=ffprobe_bin)
            if d0 is not None and d0 > MAX_CLIP_SECONDS + PEXELS_TRIM_DURATION_SLACK_SEC:
                trim_target = (body.video_trim_target or "10").strip().lower()
                if trim_target not in ("5", "10", "scene_narration"):
                    trim_target = "10"
                storage_root = Path(settings.local_storage_root).resolve()
                max_sec = resolve_pexels_trim_max_sec(
                    db,
                    settings=settings,
                    scene=sc,
                    project_id=project_id,
                    trim_target=trim_target,
                    storage_root=storage_root,
                    ffprobe_bin=ffprobe_bin,
                )
                max_sec = max(PEXELS_TRIM_MIN_SEC, min(float(max_sec), MAX_CLIP_SECONDS))
                trim_meta = {
                    "storyblocks_video_trim_target": trim_target,
                    "storyblocks_trim_max_sec": round(max_sec, 4),
                }
                try:
                    trimmed = trim_video_file_to_max_seconds(
                        tmp_path,
                        max_sec=max_sec,
                        ffmpeg_bin=ffmpeg_bin,
                        ffprobe_bin=ffprobe_bin,
                    )
                except RuntimeError as e:
                    raise StoryblocksImportError(
                        422,
                        "STORYBLOCKS_TRIM_FAILED",
                        str(e)[:2000] or "Could not trim video to scene clip length.",
                    ) from e
                tmp_path.unlink(missing_ok=True)
                tmp_path = trimmed
                suffix = ".mp4"
                asset_kind, ext_guess = classify_from_filename_and_hint(
                    f"storyblocks-video-{body.storyblocks_id}.mp4",
                    kind_hint="video",
                )

        if tmp_path.suffix.lower() in AMBIGUOUS_EXTS and asset_kind == "video":
            asset_kind = refine_ambiguous_kind(
                tmp_path,
                ffprobe_bin=ffprobe_bin,
                initial=asset_kind,
            )

        proj = db.get(Project, project_id)
        far = coerce_frame_aspect_ratio(getattr(proj, "frame_aspect_ratio", None) if proj else None)
        cff = coerce_clip_frame_fit(getattr(proj, "clip_frame_fit", None) if proj else None)
        tw, th = frame_pixel_size(far)
        frame_meta: dict[str, object] = {
            "frame_aspect_ratio": far,
            "clip_frame_fit": cff,
            "storyblocks_target_pixels": {"w": tw, "h": th},
        }
        try:
            if asset_kind == "image":
                out_media = reframe_still_image_center_crop(
                    tmp_path,
                    target_w=tw,
                    target_h=th,
                    ffmpeg_bin=ffmpeg_bin,
                    ffprobe_bin=ffprobe_bin,
                    frame_fit=cff,
                )
                if out_media != tmp_path:
                    tmp_path.unlink(missing_ok=True)
                    tmp_path = out_media
                    suffix = ".jpg"
                    asset_kind, ext_guess = classify_from_filename_and_hint(
                        f"storyblocks-photo-{body.storyblocks_id}.jpg",
                        kind_hint="image",
                    )
                    frame_meta["storyblocks_frame_fit"] = "letterbox_pad" if cff == "letterbox" else "center_cover"
                else:
                    frame_meta["storyblocks_frame_fit"] = "already_target_pixels"
            elif asset_kind == "video":
                out_media = reframe_video_center_crop(
                    tmp_path,
                    target_w=tw,
                    target_h=th,
                    ffmpeg_bin=ffmpeg_bin,
                    ffprobe_bin=ffprobe_bin,
                    frame_fit=cff,
                )
                if out_media != tmp_path:
                    tmp_path.unlink(missing_ok=True)
                    tmp_path = out_media
                    suffix = ".mp4"
                    asset_kind, ext_guess = classify_from_filename_and_hint(
                        f"storyblocks-video-{body.storyblocks_id}.mp4",
                        kind_hint="video",
                    )
                    frame_meta["storyblocks_frame_fit"] = "letterbox_pad" if cff == "letterbox" else "center_cover"
                else:
                    frame_meta["storyblocks_frame_fit"] = "already_target_pixels"
        except RuntimeError as e:
            raise StoryblocksImportError(
                422,
                "STORYBLOCKS_REFRAME_FAILED",
                str(e)[:2000] or "Could not crop Storyblocks media to the project frame.",
            ) from e

        try:
            measured_sec = assert_clip_duration_within_limit(tmp_path, asset_kind=asset_kind, ffprobe_bin=ffprobe_bin)
        except ValueError as e:
            raise StoryblocksImportError(422, "CLIP_TOO_LONG_OR_INVALID", str(e)) from e

        norm_ext = normalized_extension(asset_kind, suffix)
        asset_id = uuid.uuid4()
        storage_key = f"assets/{project_id}/{scene_id}/{asset_id}{norm_ext}"

        storage = FilesystemStorage(settings.local_storage_root)
        ct = resp_ct.strip() if isinstance(resp_ct, str) and resp_ct.strip() else None
        if not ct or ct == "application/octet-stream":
            guessed, _ = mimetypes.guess_type(orig_name)
            ct = guessed or (
                "audio/mpeg"
                if asset_kind == "audio"
                else ("video/mp4" if asset_kind == "video" else "image/jpeg")
            )
        if asset_kind == "video" and norm_ext.lower() == ".mp4":
            ct = "video/mp4"
        if asset_kind == "image" and norm_ext.lower() in (".jpg", ".jpeg"):
            ct = "image/jpeg"
        file_bytes = tmp_path.read_bytes()
        file_url = storage.put_bytes(storage_key, file_bytes, content_type=ct)

        mx = db.scalar(select(func.max(Asset.timeline_sequence)).where(Asset.scene_id == scene_id))
        next_seq = int(mx or -1) + 1

        sb_meta = storyblocks_attribution_block(
            kind=body.kind,
            storyblocks_id=body.storyblocks_id,
            title=title,
            details_url=details_url,
        )
        params: dict[str, object] = {
            "storage_key": storage_key,
            "source_filename": orig_name[:500],
            "storyblocks": sb_meta,
        }
        params.update(trim_meta)
        params.update(frame_meta)
        if measured_sec is not None:
            params["duration_sec"] = round(float(measured_sec), 4)

        a = Asset(
            id=asset_id,
            tenant_id=settings.default_tenant_id,
            scene_id=scene_id,
            project_id=project_id,
            asset_type=asset_kind,
            status="succeeded",
            generation_tier="preview",
            provider="storyblocks",
            model_name=None,
            params_json=params,
            storage_url=file_url,
            preview_url=file_url,
            error_message=None,
            timeline_sequence=next_seq,
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        log.info(
            "scene_storyblocks_imported",
            scene_id=str(scene_id),
            asset_id=str(asset_id),
            asset_type=asset_kind,
            storyblocks_kind=body.kind,
            storyblocks_id=body.storyblocks_id,
        )
        return a
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
