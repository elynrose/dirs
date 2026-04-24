"""Pexels Curated API — search and metadata (server-side only; never expose the API key to browsers)."""

from __future__ import annotations

from typing import Any, Literal

import httpx

PEXELS_API_ROOT = "https://api.pexels.com"


def pexels_auth_headers(api_key: str) -> dict[str, str]:
    k = (api_key or "").strip()
    return {"Authorization": k}


def _photo_thumb(p: dict[str, Any]) -> str | None:
    src = p.get("src")
    if isinstance(src, dict):
        for k in ("medium", "small", "tiny", "large", "large2x", "original"):
            u = src.get(k)
            if isinstance(u, str) and u.startswith("http"):
                return u
    return None


def slim_photo_result(p: dict[str, Any]) -> dict[str, Any]:
    pid = p.get("id")
    return {
        "pexels_id": int(pid) if pid is not None else 0,
        "kind": "photo",
        "width": p.get("width"),
        "height": p.get("height"),
        "photographer": p.get("photographer"),
        "photographer_url": p.get("photographer_url"),
        "pexels_url": p.get("url"),
        "alt": p.get("alt"),
        "thumb_url": _photo_thumb(p),
    }


def slim_video_result(v: dict[str, Any]) -> dict[str, Any]:
    vid = v.get("id")
    thumb = None
    imgs = v.get("image")
    if isinstance(imgs, str) and imgs.startswith("http"):
        thumb = imgs
    pics = v.get("video_pictures")
    if thumb is None and isinstance(pics, list) and pics:
        first = pics[0]
        if isinstance(first, dict):
            pic = first.get("picture") or first.get("link")
            if isinstance(pic, str) and pic.startswith("http"):
                thumb = pic
    vf = v.get("video_files")
    if thumb is None and isinstance(vf, list):
        for f in vf:
            if not isinstance(f, dict):
                continue
            if str(f.get("file_type", "")).lower() == "image/jpeg" and isinstance(f.get("link"), str):
                thumb = f["link"]
                break
    return {
        "pexels_id": int(vid) if vid is not None else 0,
        "kind": "video",
        "width": v.get("width"),
        "height": v.get("height"),
        "duration_sec": v.get("duration"),
        "photographer": v.get("user", {}).get("name") if isinstance(v.get("user"), dict) else v.get("user"),
        "photographer_url": (
            v.get("user", {}).get("url") if isinstance(v.get("user"), dict) else None
        ),
        "pexels_url": v.get("url"),
        "thumb_url": thumb,
    }


async def search_photos(
    *,
    api_key: str,
    query: str,
    page: int = 1,
    per_page: int = 15,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    params = {"query": query.strip(), "page": max(1, page), "per_page": max(1, min(per_page, 80))}
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.get(
            f"{PEXELS_API_ROOT}/v1/search",
            params=params,
            headers=pexels_auth_headers(api_key),
        )
        r.raise_for_status()
        data = r.json()
    photos = data.get("photos") or []
    slim = [slim_photo_result(p) for p in photos if isinstance(p, dict)]
    return {
        "page": data.get("page", page),
        "per_page": data.get("per_page", per_page),
        "total_results": data.get("total_results", 0),
        "next_page": data.get("next_page"),
        "results": slim,
    }


def search_photos_sync(
    *,
    api_key: str,
    query: str,
    page: int = 1,
    per_page: int = 15,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    params = {"query": query.strip(), "page": max(1, page), "per_page": max(1, min(per_page, 80))}
    with httpx.Client(timeout=timeout_sec) as client:
        r = client.get(
            f"{PEXELS_API_ROOT}/v1/search",
            params=params,
            headers=pexels_auth_headers(api_key),
        )
        r.raise_for_status()
        data = r.json()
    photos = data.get("photos") or []
    slim = [slim_photo_result(p) for p in photos if isinstance(p, dict)]
    return {
        "page": data.get("page", page),
        "per_page": data.get("per_page", per_page),
        "total_results": data.get("total_results", 0),
        "next_page": data.get("next_page"),
        "results": slim,
    }


def search_videos_sync(
    *,
    api_key: str,
    query: str,
    page: int = 1,
    per_page: int = 15,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    params = {"query": query.strip(), "page": max(1, page), "per_page": max(1, min(per_page, 80))}
    with httpx.Client(timeout=timeout_sec) as client:
        r = client.get(
            f"{PEXELS_API_ROOT}/v1/videos/search",
            params=params,
            headers=pexels_auth_headers(api_key),
        )
        r.raise_for_status()
        data = r.json()
    vids = data.get("videos") or []
    slim = [slim_video_result(v) for v in vids if isinstance(v, dict)]
    return {
        "page": data.get("page", page),
        "per_page": data.get("per_page", per_page),
        "total_results": data.get("total_results", 0),
        "next_page": data.get("next_page"),
        "results": slim,
    }


async def search_videos(
    *,
    api_key: str,
    query: str,
    page: int = 1,
    per_page: int = 15,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    params = {"query": query.strip(), "page": max(1, page), "per_page": max(1, min(per_page, 80))}
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.get(
            f"{PEXELS_API_ROOT}/v1/videos/search",
            params=params,
            headers=pexels_auth_headers(api_key),
        )
        r.raise_for_status()
        data = r.json()
    vids = data.get("videos") or []
    slim = [slim_video_result(v) for v in vids if isinstance(v, dict)]
    return {
        "page": data.get("page", page),
        "per_page": data.get("per_page", per_page),
        "total_results": data.get("total_results", 0),
        "next_page": data.get("next_page"),
        "results": slim,
    }


async def fetch_photo_json(*, api_key: str, photo_id: int, timeout_sec: float = 30.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.get(
            f"{PEXELS_API_ROOT}/v1/photos/{int(photo_id)}",
            headers=pexels_auth_headers(api_key),
        )
        r.raise_for_status()
        return r.json()


async def fetch_video_json(*, api_key: str, video_id: int, timeout_sec: float = 30.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.get(
            f"{PEXELS_API_ROOT}/v1/videos/videos/{int(video_id)}",
            headers=pexels_auth_headers(api_key),
        )
        r.raise_for_status()
        return r.json()


def pick_photo_download_url(photo: dict[str, Any]) -> tuple[str, str]:
    """Return (url, suggested_suffix) for the largest practical still."""
    src = photo.get("src")
    if not isinstance(src, dict):
        raise ValueError("photo response missing src")
    for k in ("original", "large2x", "large", "medium"):
        u = src.get(k)
        if isinstance(u, str) and u.startswith("http"):
            lower = u.lower()
            if ".jpeg" in lower or lower.endswith("jpeg"):
                return u, ".jpg"
            if ".jpg" in lower or lower.endswith("jpg"):
                return u, ".jpg"
            if ".png" in lower:
                return u, ".png"
            if ".webp" in lower:
                return u, ".webp"
            return u, ".jpg"
    raise ValueError("photo src has no usable URL")


def _quality_rank(q: str | None) -> int:
    s = (q or "").lower().strip()
    order = ("sd", "medium", "mobile", "hd", "uhd", "hls")
    try:
        return order.index(s)
    except ValueError:
        return 99


def pick_video_download_url(video: dict[str, Any]) -> tuple[str, str]:
    """Prefer a compact MP4 (sd/medium before huge HD) for ≤10s pipeline clips."""
    files = video.get("video_files")
    if not isinstance(files, list) or not files:
        raise ValueError("video response missing video_files")
    mp4: list[dict[str, Any]] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        link = f.get("link")
        ft = str(f.get("file_type", "")).lower()
        if not isinstance(link, str) or not link.startswith("http"):
            continue
        if "mp4" in ft or link.lower().rstrip().split("?", 1)[0].endswith(".mp4"):
            mp4.append(f)
    if not mp4:
        raise ValueError("no MP4 video_file entries from Pexels")
    def _w(f: dict[str, Any]) -> int:
        try:
            return int(f.get("width")) if f.get("width") is not None else 99999
        except (TypeError, ValueError):
            return 99999

    mp4.sort(
        key=lambda f: (
            _quality_rank(str(f.get("quality"))),
            _w(f),
        )
    )
    best = mp4[0]
    link = best["link"]
    assert isinstance(link, str)
    return link, ".mp4"


PhotoOrVideo = Literal["photo", "video"]


async def download_bytes_capped(
    url: str,
    *,
    max_bytes: int,
    timeout_sec: float = 120.0,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bytes, str | None]:
    """GET ``url`` into memory with a hard size cap (raises ``ValueError`` if exceeded)."""
    headers = {"User-Agent": "director-api/pexels-import"}
    if extra_headers:
        headers.update(extra_headers)
    total = 0
    parts: list[bytes] = []
    async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            ct = r.headers.get("content-type")
            async for chunk in r.aiter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"download exceeds {max_bytes} bytes")
                parts.append(chunk)
    return b"".join(parts), ct


def pexels_attribution_block(
    *,
    kind: PhotoOrVideo,
    pexels_id: int,
    photographer: str | None,
    photographer_url: str | None,
    pexels_url: str | None,
) -> dict[str, Any]:
    return {
        "id": pexels_id,
        "kind": kind,
        "photographer": photographer,
        "photographer_url": photographer_url,
        "pexels_url": pexels_url,
        "license_note": "Photos and videos on Pexels are free to use per https://www.pexels.com/license/",
    }
