"""fal image/video adapter — sync ``fal.run`` plus CDN upload for I2V inputs (``fal-client``)."""

import base64
import json
from typing import Any

import httpx
from fal_client import SyncClient

from director_api.config import Settings
from director_api.services.media_models_catalog import fal_video_endpoint_is_image_to_video
from director_api.services.project_frame import (
    coerce_frame_aspect_ratio,
    fal_aspect_ratio_string,
    fal_image_size_enum,
    fal_resolution_string,
)
from director_api.services.research_service import sanitize_jsonb_text

_FAL_DETAIL_MAX = 2000


def format_fal_result_message(res: dict[str, Any]) -> str:
    """Single line for asset/job errors: ``http_422`` plus parsed fal body when present."""
    err = str(res.get("error") or "").strip()
    det = str(res.get("detail") or "").strip()
    if err and det:
        return f"{err}: {det}"[:8000]
    return (err or det or "unknown")[:8000]


def _format_fal_http_body(text: str, max_len: int = _FAL_DETAIL_MAX) -> str:
    """Prefer JSON ``detail`` / ``message`` from fal error responses; else truncate raw text."""
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            d = obj.get("detail")
            if isinstance(d, str) and d.strip():
                return d.strip()[:max_len]
            if isinstance(d, list):
                return json.dumps(d)[:max_len]
            for k in ("message", "msg", "error"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:max_len]
                if isinstance(v, dict):
                    m = v.get("message")
                    if isinstance(m, str) and m.strip():
                        return m.strip()[:max_len]
    except (json.JSONDecodeError, TypeError):
        pass
    return raw[:max_len]


def smoke_image(settings: Settings) -> dict[str, Any]:
    if not settings.fal_key:
        return {"configured": False, "provider": "fal", "error": "FAL_KEY not set"}

    model_path = settings.fal_smoke_model.strip().lstrip("/")
    url = f"https://fal.run/{model_path}"
    body = {"prompt": "smoke test, abstract gradient, no text, tiny detail"}
    try:
        with httpx.Client(timeout=180.0) as client:
            r = client.post(
                url,
                headers={
                    "Authorization": f"Key {settings.fal_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as e:
        return {
            "configured": True,
            "provider": "fal",
            "model": model_path,
            "error": "http_client_error",
            "detail": str(e)[:_FAL_DETAIL_MAX],
        }
    if r.status_code >= 400:
        return {
            "configured": True,
            "provider": "fal",
            "model": model_path,
            "error": f"http_{r.status_code}",
            "detail": _format_fal_http_body(r.text),
            "body": r.text[:_FAL_DETAIL_MAX],
        }
    data = r.json()
    return {
        "configured": True,
        "provider": "fal",
        "model": model_path,
        "response_keys": list(data.keys()) if isinstance(data, dict) else [],
        "raw_preview": str(data)[:1200],
    }


def smoke_video(
    settings: Settings,
    *,
    download: bool = False,
    model_path: str | None = None,
) -> dict[str, Any]:
    """
    Probe text-to-video via ``fal.run`` (no scene image). Skipped when ``FAL_VIDEO_MODEL`` is I2V-only.

    With ``download=False`` (default), only checks HTTP + JSON for a video URL — no MP4 download
    (faster, cheaper smoke). With ``download=True``, fetches bytes to verify CDN reachability.
    """
    if not settings.fal_key:
        return {"configured": False, "provider": "fal", "error": "FAL_KEY not set"}

    mp = (model_path or settings.fal_video_model or "fal-ai/minimax/video-01-live").strip().lstrip("/")
    if fal_model_is_image_to_video(mp):
        return {
            "configured": True,
            "provider": "fal",
            "model": mp,
            "skipped": True,
            "reason": (
                "FAL_VIDEO_MODEL is image-to-video; smoke_video only exercises text-to-video. "
                "Set fal_video_model to a T2V endpoint_id for this check, or validate I2V via a scene video job."
            ),
        }

    url = f"https://fal.run/{mp}"
    prompt = "smoke test, abstract soft gradients, no people, no text, 2 seconds"
    dur = max(1, min(8, 5))
    full_body: dict[str, Any] = {
        "prompt": prompt,
        "duration": dur,
        "aspect_ratio": "16:9",
        "resolution": "1280x720",
    }
    minimal_body: dict[str, Any] = {"prompt": prompt, "duration": dur}
    aspect_body: dict[str, Any] = {"prompt": prompt, "duration": dur, "aspect_ratio": "16:9"}
    headers = {"Authorization": f"Key {settings.fal_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=600.0) as client:
            r = client.post(url, headers=headers, json=full_body)
            if r.status_code >= 400:
                r = client.post(url, headers=headers, json=minimal_body)
            if r.status_code >= 400:
                r = client.post(url, headers=headers, json=aspect_body)
    except httpx.HTTPError as e:
        return {
            "configured": True,
            "provider": "fal",
            "model": mp,
            "error": "http_client_error",
            "detail": str(e)[:_FAL_DETAIL_MAX],
        }

    if r.status_code >= 400:
        return {
            "configured": True,
            "provider": "fal",
            "model": mp,
            "error": f"http_{r.status_code}",
            "detail": _format_fal_http_body(r.text),
        }
    try:
        data = r.json()
    except Exception:
        return {
            "configured": True,
            "provider": "fal",
            "model": mp,
            "error": "invalid_json",
            "detail": _format_fal_http_body(r.text),
        }
    media_url = _extract_media_url(data)
    if not media_url:
        return {
            "configured": True,
            "provider": "fal",
            "model": mp,
            "error": "no_video_url",
            "detail": str(data)[:_FAL_DETAIL_MAX],
        }
    out: dict[str, Any] = {
        "configured": True,
        "provider": "fal",
        "model": mp,
        "video_url": media_url,
        "downloaded": False,
    }
    if not download:
        return out
    try:
        with httpx.Client(timeout=600.0, follow_redirects=True) as client:
            vr = client.get(media_url)
    except httpx.HTTPError as e:
        out["error"] = "video_download_http_error"
        out["detail"] = str(e)[:_FAL_DETAIL_MAX]
        return out
    if vr.status_code >= 400:
        out["error"] = f"download_http_{vr.status_code}"
        out["detail"] = media_url[:256]
        return out
    raw = vr.content
    if not raw or len(raw) < 32:
        out["error"] = "empty_or_tiny_video_download"
        out["detail"] = f"len={len(raw or b'')}"
        return out
    out["downloaded"] = True
    out["bytes_len"] = len(raw)
    out["content_type"] = vr.headers.get("content-type") or "video/mp4"
    return out


def _http_url(s: str | None) -> str | None:
    if isinstance(s, str) and s.strip().startswith(("http://", "https://")):
        return s.strip()
    return None


def _extract_image_url(data: Any, depth: int = 0) -> str | None:
    """Resolve image CDN URL from fal.run JSON (handles nested ``data`` / ``output`` wrappers)."""
    if depth > 6 or not isinstance(data, dict):
        return None
    u = _http_url(data.get("url"))
    if u:
        return u
    for key in ("images", "image", "output", "data", "result", "outputs"):
        v = data.get(key)
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, dict):
                for uk in ("url", "file_url", "image_url"):
                    u2 = _http_url(first.get(uk))
                    if u2:
                        return u2
                nested = _extract_image_url(first, depth + 1)
                if nested:
                    return nested
            if isinstance(first, str):
                u2 = _http_url(first)
                if u2:
                    return u2
        if isinstance(v, dict):
            nested = _extract_image_url(v, depth + 1)
            if nested:
                return nested
            u2 = _http_url(v.get("url"))
            if u2:
                return u2
        if isinstance(v, str):
            u2 = _http_url(v)
            if u2:
                return u2
    return None


def generate_scene_image(
    settings: Settings,
    prompt: str,
    *,
    model_path: str | None = None,
    negative_prompt: str | None = None,
    frame_aspect_ratio: str | None = None,
) -> dict[str, Any]:
    """
    Sync image generation via fal.run. Returns {ok, bytes?, content_type?, error?, model?}.
    """
    if not settings.fal_key:
        return {"ok": False, "error": "FAL_KEY not set", "provider": "fal"}
    model_path = (model_path or settings.fal_smoke_model or "fal-ai/fast-sdxl").strip().lstrip("/")
    url = f"https://fal.run/{model_path}"
    neg = sanitize_jsonb_text((negative_prompt or "").strip(), 2000)
    far = coerce_frame_aspect_ratio(frame_aspect_ratio)
    ar = fal_aspect_ratio_string(far)
    size_enum = fal_image_size_enum(far)
    w, h = (1280, 720) if far == "16:9" else (720, 1280)
    body: dict[str, object] = {
        "prompt": prompt[:4000],
        "aspect_ratio": ar,
        "image_size": size_enum,
        "width": w,
        "height": h,
    }
    if neg:
        body["negative_prompt"] = neg
    minimal: dict[str, object] = {"prompt": prompt[:4000]}
    if neg:
        minimal["negative_prompt"] = neg
    aspect_only: dict[str, object] = {"prompt": prompt[:4000], "aspect_ratio": ar}
    if neg:
        aspect_only["negative_prompt"] = neg
    try:
        with httpx.Client(timeout=180.0) as client:
            r = client.post(
                url,
                headers={
                    "Authorization": f"Key {settings.fal_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if r.status_code >= 400:
                r = client.post(
                    url,
                    headers={
                        "Authorization": f"Key {settings.fal_key}",
                        "Content-Type": "application/json",
                    },
                    json=minimal,
                )
            if r.status_code >= 400:
                r = client.post(
                    url,
                    headers={
                        "Authorization": f"Key {settings.fal_key}",
                        "Content-Type": "application/json",
                    },
                    json=aspect_only,
                )
    except httpx.HTTPError as e:
        return {
            "ok": False,
            "provider": "fal",
            "model": model_path,
            "error": "http_client_error",
            "detail": str(e)[:_FAL_DETAIL_MAX],
        }
    if r.status_code >= 400:
        return {
            "ok": False,
            "provider": "fal",
            "model": model_path,
            "error": f"http_{r.status_code}",
            "detail": _format_fal_http_body(r.text),
        }
    try:
        data = r.json()
    except Exception:
        return {
            "ok": False,
            "provider": "fal",
            "error": "invalid_json",
            "detail": _format_fal_http_body(r.text),
        }
    img_url = _extract_image_url(data)
    if not img_url:
        return {
            "ok": False,
            "provider": "fal",
            "model": model_path,
            "error": "no_image_url",
            "detail": str(data)[:_FAL_DETAIL_MAX],
        }
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            ir = client.get(img_url)
    except httpx.HTTPError as e:
        return {
            "ok": False,
            "provider": "fal",
            "model": model_path,
            "error": "image_download_http_error",
            "detail": str(e)[:_FAL_DETAIL_MAX],
        }
    if ir.status_code >= 400:
        return {
            "ok": False,
            "provider": "fal",
            "model": model_path,
            "error": f"download_http_{ir.status_code}",
            "detail": img_url[:256],
        }
    raw = ir.content
    if not raw or len(raw) < 32:
        return {
            "ok": False,
            "provider": "fal",
            "model": model_path,
            "error": "empty_or_tiny_image_download",
            "detail": f"url={img_url[:120]!r} len={len(raw or b'')}",
        }
    return {
        "ok": True,
        "provider": "fal",
        "model": model_path,
        "bytes": raw,
        "content_type": ir.headers.get("content-type") or "image/png",
    }


def fal_model_is_image_to_video(model_path: str | None) -> bool:
    """True when the fal endpoint is image→video (needs ``image_url``, not T2V-only fields).

    Uses ``data/media_models_catalog.json`` (fal Platform ``category``) so ids like
    ``fal-ai/ai-avatar`` or ``.../reference-to-video`` are recognized without ``image-to-video``
    in the path string.
    """
    mp = (model_path or "").strip().lstrip("/")
    if not mp:
        return False
    resolved = fal_video_endpoint_is_image_to_video(mp)
    if resolved is not None:
        return resolved
    lp = mp.lower()
    return (
        "image-to-video" in lp
        or "/i2v" in lp
        or "image_to_video" in lp
        or "reference-to-video" in lp
    )


def _guess_image_mime(data: bytes) -> str:
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _image_to_data_uri(data: bytes, content_type: str | None) -> str:
    ct = (content_type or "").strip() or _guess_image_mime(data)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{ct};base64,{b64}"


def _build_image_to_video_json_body(
    model_path: str, prompt: str, image_url: str, duration_sec: float
) -> dict[str, Any]:
    """Build JSON body for fal image-to-video models (schemas differ; Hailuo uses string duration enum)."""
    mp = model_path.lower()
    prompt_s = prompt[:4000]
    if "hailuo" in mp or ("minimax" in mp and "image-to-video" in mp):
        d = max(1, min(int(round(duration_sec)), 30))
        dur_s = "6" if d <= 6 else "10"
        return {
            "prompt": prompt_s,
            "image_url": image_url,
            "duration": dur_s,
            "resolution": "768P",
            "prompt_optimizer": True,
        }
    return {
        "prompt": prompt_s,
        "image_url": image_url,
        "duration": max(1, min(int(duration_sec), 30)),
    }


def _extract_media_url(data: object, depth: int = 0) -> str | None:
    if depth > 8 or not isinstance(data, dict):
        return None
    for key in ("videos", "video", "images", "image", "output", "data"):
        v = data.get(key)
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, dict):
                for k in ("url", "video_url", "image_url"):
                    if isinstance(first.get(k), str):
                        return first[k]
                nested = _extract_media_url(first, depth + 1)
                if nested:
                    return nested
            if isinstance(first, str) and first.startswith("http"):
                return first
        if isinstance(v, dict):
            for k in ("url", "video_url", "image_url"):
                if isinstance(v.get(k), str):
                    return v[k]
            nested = _extract_media_url(v, depth + 1)
            if nested:
                return nested
        if isinstance(v, str) and v.startswith("http"):
            return v
    for k in ("url", "video_url", "image_url"):
        if isinstance(data.get(k), str):
            return data[k]
    for v in data.values():
        if isinstance(v, dict):
            nested = _extract_media_url(v, depth + 1)
            if nested:
                return nested
    return None


def generate_scene_video_fal(
    settings: Settings,
    prompt: str,
    duration_sec: float,
    *,
    model: str | None = None,
    image_url: str | None = None,
    image_bytes: bytes | None = None,
    image_content_type: str | None = None,
    frame_aspect_ratio: str | None = None,
) -> dict[str, Any]:
    """Sync video via fal.run (text-to-video or image-to-video). Returns {ok, bytes?, content_type?, error?, model?}."""
    if not settings.fal_key:
        return {"ok": False, "provider": "fal", "error": "FAL_KEY not set"}
    model_path = (model or settings.fal_video_model or "fal-ai/minimax/video-01-live").strip().lstrip("/")
    url = f"https://fal.run/{model_path}"
    is_i2v = fal_model_is_image_to_video(model_path)

    if is_i2v:
        img_url: str | None = None
        if image_url and _http_url(image_url):
            img_url = image_url.strip()
        elif image_bytes and len(image_bytes) >= 32:
            ct = (image_content_type or "").strip() or _guess_image_mime(image_bytes)
            ctl = ct.lower()
            ext = (
                "jpg"
                if "jpeg" in ctl or ctl == "image/jpg"
                else "png"
                if "png" in ctl
                else "webp"
                if "webp" in ctl
                else "jpg"
            )
            try:
                img_url = SyncClient(key=settings.fal_key, default_timeout=180.0).upload(
                    image_bytes, ct, file_name=f"director-scene.{ext}"
                )
            except Exception:
                img_url = _image_to_data_uri(image_bytes, image_content_type)
        if not img_url:
            return {
                "ok": False,
                "provider": "fal",
                "model": model_path,
                "error": "image_to_video_missing_image",
                "detail": "This model is image-to-video: provide a scene image (or image_url).",
            }
        body_i2v = _build_image_to_video_json_body(model_path, prompt, img_url, duration_sec)
        minimal_i2v: dict[str, Any] = {"prompt": prompt[:4000], "image_url": img_url}
        try:
            with httpx.Client(timeout=600.0) as client:
                r = client.post(
                    url,
                    headers={"Authorization": f"Key {settings.fal_key}", "Content-Type": "application/json"},
                    json=body_i2v,
                )
                if r.status_code >= 400:
                    r = client.post(
                        url,
                        headers={"Authorization": f"Key {settings.fal_key}", "Content-Type": "application/json"},
                        json=minimal_i2v,
                    )
            if r.status_code >= 400:
                return {
                    "ok": False,
                    "provider": "fal",
                    "model": model_path,
                    "error": f"http_{r.status_code}",
                    "detail": _format_fal_http_body(r.text),
                }
            data = r.json()
            media_url = _extract_media_url(data)
            if not media_url:
                return {
                    "ok": False,
                    "provider": "fal",
                    "model": model_path,
                    "error": "no_video_url",
                    "detail": str(data)[:_FAL_DETAIL_MAX],
                }
            try:
                with httpx.Client(timeout=600.0, follow_redirects=True) as client:
                    vr = client.get(media_url)
            except httpx.HTTPError as e:
                return {
                    "ok": False,
                    "provider": "fal",
                    "model": model_path,
                    "error": "video_download_http_error",
                    "detail": str(e)[:_FAL_DETAIL_MAX],
                }
            if vr.status_code >= 400:
                return {
                    "ok": False,
                    "provider": "fal",
                    "model": model_path,
                    "error": f"download_http_{vr.status_code}",
                    "detail": media_url[:256],
                }
            return {
                "ok": True,
                "provider": "fal",
                "model": model_path,
                "bytes": vr.content,
                "content_type": vr.headers.get("content-type") or "video/mp4",
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "provider": "fal", "model": model_path, "error": str(e)[:8000]}

    d = max(1, min(int(duration_sec), 30))
    p = prompt[:4000]
    far = coerce_frame_aspect_ratio(frame_aspect_ratio)
    ar = fal_aspect_ratio_string(far)
    res_s = fal_resolution_string(far)
    body = {
        "prompt": p,
        "duration": d,
        "aspect_ratio": ar,
        "resolution": res_s,
    }
    aspect_body = {"prompt": p, "duration": d, "aspect_ratio": ar}
    minimal_body = {"prompt": p, "duration": d}
    try:
        with httpx.Client(timeout=600.0) as client:
            r = client.post(
                url,
                headers={"Authorization": f"Key {settings.fal_key}", "Content-Type": "application/json"},
                json=body,
            )
            if r.status_code >= 400:
                r = client.post(
                    url,
                    headers={"Authorization": f"Key {settings.fal_key}", "Content-Type": "application/json"},
                    json=aspect_body,
                )
            if r.status_code >= 400:
                r = client.post(
                    url,
                    headers={"Authorization": f"Key {settings.fal_key}", "Content-Type": "application/json"},
                    json=minimal_body,
                )
        if r.status_code >= 400:
            return {
                "ok": False,
                "provider": "fal",
                "model": model_path,
                "error": f"http_{r.status_code}",
                "detail": _format_fal_http_body(r.text),
            }
        data = r.json()
        media_url = _extract_media_url(data)
        if not media_url:
            return {
                "ok": False,
                "provider": "fal",
                "model": model_path,
                "error": "no_video_url",
                "detail": str(data)[:_FAL_DETAIL_MAX],
            }
        try:
            with httpx.Client(timeout=600.0, follow_redirects=True) as client:
                vr = client.get(media_url)
        except httpx.HTTPError as e:
            return {
                "ok": False,
                "provider": "fal",
                "model": model_path,
                "error": "video_download_http_error",
                "detail": str(e)[:_FAL_DETAIL_MAX],
            }
        if vr.status_code >= 400:
            return {
                "ok": False,
                "provider": "fal",
                "model": model_path,
                "error": f"download_http_{vr.status_code}",
                "detail": media_url[:256],
            }
        return {
            "ok": True,
            "provider": "fal",
            "model": model_path,
            "bytes": vr.content,
            "content_type": vr.headers.get("content-type") or "video/mp4",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "provider": "fal", "model": model_path, "error": str(e)[:8000]}
