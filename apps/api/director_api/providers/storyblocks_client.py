"""Storyblocks partner API (VideoBlocks / GraphicStock) — HMAC-signed search and download URLs.

See Storyblocks API docs (search ``/api/v1/stock-items/search/``, download ``/api/v1/stock-items/download/...``).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

DEFAULT_STORYBLOCKS_VIDEO_API_BASE = "https://api.videoblocks.com"
DEFAULT_STORYBLOCKS_IMAGE_API_BASE = "https://api.graphicstock.com"

# HMAC signs this exact path (including trailing slash) per partner documentation.
SEARCH_RESOURCE = "/api/v1/stock-items/search/"


def storyblocks_hmac_hex(*, private_key: str, expires: int, resource: str) -> str:
    raw_key = f"{(private_key or '').strip()}{int(expires)}"
    return hmac.new(
        raw_key.encode("utf-8"),
        resource.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _auth_query_params(*, public_key: str, private_key: str, resource: str) -> dict[str, str]:
    exp = int(time.time())
    return {
        "APIKEY": (public_key or "").strip(),
        "EXPIRES": str(exp),
        "HMAC": storyblocks_hmac_hex(private_key=private_key, expires=exp, resource=resource),
    }


def _build_signed_url(*, base: str, resource_path: str, query: dict[str, Any]) -> str:
    b = (base or "").strip().rstrip("/")
    auth = _auth_query_params(
        public_key=str(query.pop("_public_key")),
        private_key=str(query.pop("_private_key")),
        resource=resource_path,
    )
    flat: dict[str, str] = {**auth}
    for k, v in query.items():
        if v is None:
            continue
        flat[str(k)] = str(v)
    return f"{b}{resource_path}?{urlencode(flat)}"


def _parse_dimensions(item: dict[str, Any]) -> tuple[int | None, int | None]:
    ar = item.get("aspect_ratio") if item.get("aspect_ratio") is not None else item.get("aspectRatio")
    if isinstance(ar, (int, float)) and ar and ar > 0:
        w = 1920
        h = int(round(w / float(ar)))
        return w, h
    if isinstance(ar, str) and ":" in ar:
        parts = ar.replace(" ", "").split(":", 1)
        try:
            aw, ah = float(parts[0]), float(parts[1])
            if aw > 0 and ah > 0:
                w = 1920
                h = int(round(w * ah / aw))
                return w, h
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return None, None


def slim_storyblocks_photo(item: dict[str, Any]) -> dict[str, Any]:
    iid = item.get("id")
    w, h = _parse_dimensions(item)
    thumb = item.get("thumbnail_url") if isinstance(item.get("thumbnail_url"), str) else None
    if not thumb and isinstance(item.get("preview_url"), str):
        thumb = item["preview_url"]
    return {
        "provider": "storyblocks",
        "storyblocks_id": int(iid) if iid is not None else 0,
        "kind": "photo",
        "width": w,
        "height": h,
        "title": item.get("title") if isinstance(item.get("title"), str) else None,
        "details_url": item.get("details_url") if isinstance(item.get("details_url"), str) else None,
        "thumb_url": thumb,
        "duration_sec": None,
    }


def slim_storyblocks_video(item: dict[str, Any]) -> dict[str, Any]:
    iid = item.get("id")
    w, h = _parse_dimensions(item)
    thumb = item.get("thumbnail_url") if isinstance(item.get("thumbnail_url"), str) else None
    if not thumb and isinstance(item.get("preview_url"), str):
        thumb = item["preview_url"]
    dur = item.get("duration")
    try:
        dsec = int(dur) if dur is not None else None
    except (TypeError, ValueError):
        dsec = None
    return {
        "provider": "storyblocks",
        "storyblocks_id": int(iid) if iid is not None else 0,
        "kind": "video",
        "width": w,
        "height": h,
        "title": item.get("title") if isinstance(item.get("title"), str) else None,
        "details_url": item.get("details_url") if isinstance(item.get("details_url"), str) else None,
        "thumb_url": thumb,
        "duration_sec": dsec,
    }


async def search_photos(
    *,
    public_key: str,
    private_key: str,
    base_url: str,
    query: str,
    page: int = 1,
    per_page: int = 20,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    n = max(1, min(int(per_page), 100))
    p = max(1, int(page))
    q = {
        "_public_key": public_key,
        "_private_key": private_key,
        "keywords": (query or "").strip(),
        "page": p,
        "num_results": n,
        "content_type": "photos",
    }
    url = _build_signed_url(base=base_url, resource_path=SEARCH_RESOURCE, query=dict(q))
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict) or not data.get("success"):
        msg = data.get("message") if isinstance(data, dict) else "invalid response"
        raise ValueError(str(msg)[:500])
    info = data.get("info") or []
    rows: list[dict[str, Any]] = []
    for x in info:
        if not isinstance(x, dict) or x.get("id") is None:
            continue
        try:
            if int(x["id"]) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        rows.append(slim_storyblocks_photo(x))
    return {
        "page": p,
        "per_page": n,
        "total_results": int(data.get("totalSearchResults") or 0),
        "next_page": p + 1 if p * n < int(data.get("totalSearchResults") or 0) else None,
        "results": rows,
    }


async def search_videos(
    *,
    public_key: str,
    private_key: str,
    base_url: str,
    query: str,
    page: int = 1,
    per_page: int = 15,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    n = max(1, min(int(per_page), 100))
    p = max(1, int(page))
    q = {
        "_public_key": public_key,
        "_private_key": private_key,
        "keywords": (query or "").strip(),
        "page": p,
        "num_results": n,
        "content_type": "footage,motionbackgrounds",
    }
    url = _build_signed_url(base=base_url, resource_path=SEARCH_RESOURCE, query=dict(q))
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict) or not data.get("success"):
        msg = data.get("message") if isinstance(data, dict) else "invalid response"
        raise ValueError(str(msg)[:500])
    info = data.get("info") or []
    rows = []
    for x in info:
        if not isinstance(x, dict) or x.get("id") is None:
            continue
        try:
            if int(x["id"]) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        rows.append(slim_storyblocks_video(x))
    total = int(data.get("totalSearchResults") or 0)
    return {
        "page": p,
        "per_page": n,
        "total_results": total,
        "next_page": p + 1 if p * n < total else None,
        "results": rows,
    }


def _download_resource_path(stock_item_id: int, downloader_id: int) -> str:
    return f"/api/v1/stock-items/download/{int(stock_item_id)}/{int(downloader_id)}"


async def fetch_signed_download_url(
    *,
    public_key: str,
    private_key: str,
    base_url: str,
    stock_item_id: int,
    downloader_id: int,
    timeout_sec: float = 45.0,
) -> str:
    path = _download_resource_path(stock_item_id, downloader_id)
    q: dict[str, Any] = {"_public_key": public_key, "_private_key": private_key}
    url = _build_signed_url(base=base_url, resource_path=path, query=q)
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict) or not data.get("success"):
        msg = data.get("message") if isinstance(data, dict) else "invalid response"
        raise ValueError(str(msg)[:500])
    info = data.get("info")
    if isinstance(info, dict):
        u = info.get("url")
        if isinstance(u, str) and u.startswith("http"):
            return u
    raise ValueError("download response missing info.url")


async def fetch_stock_item_json(
    *,
    public_key: str,
    private_key: str,
    base_url: str,
    stock_item_id: int,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    path = f"/api/v1/stock-items/{int(stock_item_id)}"
    q: dict[str, Any] = {"_public_key": public_key, "_private_key": private_key}
    url = _build_signed_url(base=base_url, resource_path=path, query=dict(q))
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict) or not data.get("success"):
        msg = data.get("message") if isinstance(data, dict) else "invalid response"
        raise ValueError(str(msg)[:500])
    info = data.get("info")
    if isinstance(info, dict):
        return info
    raise ValueError("stock item response missing info")


def storyblocks_attribution_block(
    *,
    kind: str,
    storyblocks_id: int,
    title: str | None,
    details_url: str | None,
) -> dict[str, Any]:
    return {
        "id": storyblocks_id,
        "kind": kind,
        "title": title,
        "details_url": details_url,
        "license_note": "Storyblocks content is licensed per your Storyblocks API / partner agreement.",
    }
