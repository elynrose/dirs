"""
Persistent JSON catalog for Studio media model lists (fal Platform API).

Fal models are fetched via sync and written to ``data/media_models_catalog.json`` at the repo root.
GET /v1/fal/models reads from disk (no live HTTP per request). Use POST /v1/fal/models/sync to refresh.

ComfyUI has no standard public model catalog; the JSON file includes a ``comfyui`` section for optional
curated workflow metadata (edit by hand or tooling).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from director_api.config import Settings, get_settings
from director_api.services.runtime_settings import resolve_runtime_settings

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
CATALOG_PATH = _REPO_ROOT / "data" / "media_models_catalog.json"

FAL_PLATFORM_MODELS = "https://api.fal.ai/v1/models"
FAL_CATALOG_TEXT_TO_IMAGE = "text-to-image"
FAL_CATALOG_IMAGE_TO_IMAGE = "image-to-image"
FAL_CATALOG_TEXT_TO_VIDEO = "text-to-video"
FAL_CATALOG_IMAGE_TO_VIDEO = "image-to-video"
# Discovery is public; optional FAL_KEY raises rate limits. Invalid keys get HTTP 401 — we retry without auth.
FAL_STUDIO_IMAGE_CATEGORIES: tuple[str, ...] = (
    FAL_CATALOG_TEXT_TO_IMAGE,
    FAL_CATALOG_IMAGE_TO_IMAGE,
)
FAL_STUDIO_VIDEO_CATEGORIES: tuple[str, ...] = (FAL_CATALOG_TEXT_TO_VIDEO, FAL_CATALOG_IMAGE_TO_VIDEO)


def default_catalog() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "fal": {
            "image": {
                "models": [],
                "fal_categories": list(FAL_STUDIO_IMAGE_CATEGORIES),
            },
            "video": {
                "models": [],
                "fal_categories": list(FAL_STUDIO_VIDEO_CATEGORIES),
            },
        },
        "comfyui": {
            "note": "Optional curated entries; image/video use COMFYUI_* workflow paths in settings.",
            "workflows": [],
        },
    }


def _ensure_parent() -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def read_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.is_file():
        return default_catalog()
    try:
        raw = CATALOG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("fal"), dict):
            return data
    except (OSError, json.JSONDecodeError) as e:
        log.warning("media_catalog_read_failed", path=str(CATALOG_PATH), error=str(e)[:200])
    return default_catalog()


def write_catalog(data: dict[str, Any]) -> None:
    _ensure_parent()
    tmp = CATALOG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, CATALOG_PATH)


def _catalog_mtime_age_sec() -> int | None:
    try:
        st = CATALOG_PATH.stat()
        return int(max(0.0, time.time() - st.st_mtime))
    except OSError:
        return None


def _get_models_page(
    *,
    params: dict[str, str | int],
    headers: dict[str, str],
) -> httpx.Response:
    """GET /v1/models with 429 backoff. On 401 with ``Authorization``, clear headers and retry once."""
    delay = 1.0
    for _ in range(8):
        r = httpx.get(FAL_PLATFORM_MODELS, params=params, headers=headers or {}, timeout=90.0)
        if r.status_code == 401 and headers.get("Authorization"):
            log.warning("fal_catalog_401_retrying_without_key", category=params.get("category"))
            headers.clear()
            continue
        if r.status_code == 429:
            time.sleep(delay)
            delay = min(delay * 2.0, 30.0)
            continue
        return r
    return r


def _fetch_category(
    category: str,
    *,
    headers: dict[str, str] | None = None,
    max_pages: int = 80,
) -> list[dict[str, Any]]:
    """List active models for one Platform ``category`` (paginated)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor: str | None = None
    hdr = dict(headers or {})
    for _ in range(max_pages):
        params: dict[str, str | int] = {
            "category": category,
            "status": "active",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        r = _get_models_page(params=params, headers=hdr)
        if r.status_code >= 400:
            raise RuntimeError(f"fal catalog HTTP {r.status_code}: {(r.text or '')[:500]}")
        body = r.json()
        if not isinstance(body, dict):
            raise RuntimeError("fal catalog response is not a JSON object")
        for m in body.get("models") or []:
            eid = m.get("endpoint_id")
            if not isinstance(eid, str) or not eid.strip() or eid in seen:
                continue
            seen.add(eid)
            meta = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
            out.append(
                {
                    "endpoint_id": eid.strip(),
                    "display_name": str(meta.get("display_name") or eid).strip(),
                    "category": str(meta.get("category") or category),
                }
            )
        # Follow pagination by cursor until exhausted (do not rely on has_more alone).
        nxt = body.get("next_cursor")
        if isinstance(nxt, str) and nxt.strip():
            cursor = nxt.strip()
            time.sleep(0.12)
            continue
        cursor = None
        break
    out.sort(key=lambda x: (x["display_name"].lower(), x["endpoint_id"]))
    return out


def _fetch_categories_merged(
    categories: tuple[str, ...],
    *,
    headers: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    by_id: dict[str, dict[str, Any]] = {}
    for cat in categories:
        for row in _fetch_category(cat, headers=headers):
            by_id[row["endpoint_id"]] = row
        time.sleep(0.25)
    merged = sorted(by_id.values(), key=lambda x: (x["display_name"].lower(), x["endpoint_id"]))
    return merged, categories


def sync_fal_catalog_from_api(db: Any, settings: Settings, user_id: int | None = None) -> dict[str, Any]:
    """Fetch fal image + video catalogs and persist to JSON. Returns summary."""
    tid = (getattr(settings, "default_tenant_id", None) or "").strip()
    eff = resolve_runtime_settings(db, get_settings(), tid or None, user_id=user_id)
    headers: dict[str, str] = {}
    key = (eff.fal_key or "").strip()
    if key:
        headers["Authorization"] = f"Key {key}"

    log.debug(
        "media_catalog_sync_start",
        tenant_id=getattr(settings, "default_tenant_id", None),
        catalog_auth=bool(headers.get("Authorization")),
    )

    img_models, img_cats = _fetch_categories_merged(FAL_STUDIO_IMAGE_CATEGORIES, headers=headers)
    vid_models, vid_cats = _fetch_categories_merged(FAL_STUDIO_VIDEO_CATEGORIES, headers=headers)

    base = read_catalog()
    if not isinstance(base.get("fal"), dict):
        base = default_catalog()
    base.setdefault("comfyui", default_catalog()["comfyui"])
    base["fal"] = {
        "image": {"models": img_models, "fal_categories": list(img_cats)},
        "video": {"models": vid_models, "fal_categories": list(vid_cats)},
    }
    base["updated_at"] = datetime.now(timezone.utc).isoformat()
    base["version"] = int(base.get("version") or 1)
    write_catalog(base)
    log.info(
        "media_models_catalog_synced",
        path=str(CATALOG_PATH),
        image_count=len(img_models),
        video_count=len(vid_models),
    )
    return {
        "ok": True,
        "path": str(CATALOG_PATH),
        "image_model_count": len(img_models),
        "video_model_count": len(vid_models),
        "updated_at": base["updated_at"],
    }


def fal_video_endpoint_is_image_to_video(endpoint_id: str | None) -> bool | None:
    """
    Look up ``endpoint_id`` in the on-disk video catalog.

    Returns ``True`` if ``category`` is ``image-to-video``, ``False`` if ``text-to-video``,
    ``None`` if the endpoint is not listed (caller may fall back to id heuristics).
    """
    eid = (endpoint_id or "").strip().lstrip("/")
    if not eid:
        return None
    row = get_fal_models_for_media("video")
    for m in row.get("models") or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("endpoint_id") or "").strip().lstrip("/")
        if mid != eid:
            continue
        c = str(m.get("category") or "").lower()
        if c == "image-to-video":
            return True
        if c == "text-to-video":
            return False
        return None
    return None


def get_fal_models_for_media(media: Literal["image", "video"]) -> dict[str, Any]:
    """Models + metadata for one media type from on-disk catalog."""
    cat = read_catalog()
    fal = cat.get("fal") if isinstance(cat.get("fal"), dict) else {}
    key = "image" if media == "image" else "video"
    block = fal.get(key) if isinstance(fal.get(key), dict) else {}
    models = list(block.get("models") or [])
    fc = block.get("fal_categories")
    fal_categories = list(fc) if isinstance(fc, list) else (
        list(FAL_STUDIO_IMAGE_CATEGORIES) if media == "image" else list(FAL_STUDIO_VIDEO_CATEGORIES)
    )
    age = _catalog_mtime_age_sec()
    return {
        "media": media,
        "models": models,
        "fal_categories": fal_categories,
        "catalog_path": str(CATALOG_PATH),
        "catalog_updated_at": cat.get("updated_at"),
        "cache_age_sec": age if age is not None else 0,
        "needs_sync": len(models) == 0,
    }
