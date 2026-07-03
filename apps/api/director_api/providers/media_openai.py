"""OpenAI image adapter — sync ``images/generations`` (gpt-image-1). Returns the standard media dict."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from director_api.config import Settings
from director_api.services.project_frame import coerce_frame_aspect_ratio

_DETAIL_MAX = 2000
_DEFAULT_BASE = "https://api.openai.com/v1"


def _base_url(settings: Settings) -> str:
    raw = (getattr(settings, "openai_api_base_url", "") or "").strip().rstrip("/")
    return raw or _DEFAULT_BASE


def _openai_size(aspect: str | None) -> str:
    # gpt-image-1 supports 1024x1024, 1536x1024 (landscape), 1024x1536 (portrait).
    return "1024x1536" if coerce_frame_aspect_ratio(aspect) == "9:16" else "1536x1024"


def _http_body(text: str) -> str:
    return (text or "").strip()[:_DETAIL_MAX]


def generate_scene_image(
    settings: Settings,
    prompt: str,
    *,
    model_path: str | None = None,
    negative_prompt: str | None = None,
    frame_aspect_ratio: str | None = None,
) -> dict[str, Any]:
    """Sync image generation via OpenAI. Returns {ok, bytes?, content_type?, provider, model?, error?, detail?}."""
    api_key = (getattr(settings, "openai_api_key", None) or "").strip()
    if not api_key:
        return {"ok": False, "provider": "openai", "error": "OPENAI_API_KEY not set"}
    model = (model_path or getattr(settings, "openai_image_model", None) or "gpt-image-1").strip()
    p = (prompt or "").strip()[:4000]
    neg = (negative_prompt or "").strip()
    if neg:
        p = f"{p}\n\nAvoid: {neg[:1000]}"
    body: dict[str, Any] = {
        "model": model,
        "prompt": p,
        "n": 1,
        "size": _openai_size(frame_aspect_ratio),
    }
    url = f"{_base_url(settings)}/images/generations"
    try:
        with httpx.Client(timeout=180.0) as client:
            r = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as e:
        return {"ok": False, "provider": "openai", "model": model, "error": "http_client_error", "detail": str(e)[:_DETAIL_MAX]}
    if r.status_code >= 400:
        return {"ok": False, "provider": "openai", "model": model, "error": f"http_{r.status_code}", "detail": _http_body(r.text)}
    try:
        data = r.json()
    except ValueError:
        return {"ok": False, "provider": "openai", "model": model, "error": "invalid_json", "detail": _http_body(r.text)}
    items = data.get("data") if isinstance(data, dict) else None
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if not first:
        return {"ok": False, "provider": "openai", "model": model, "error": "no_image", "detail": str(data)[:_DETAIL_MAX]}
    b64 = first.get("b64_json")
    if isinstance(b64, str) and b64:
        try:
            raw = base64.b64decode(b64)
        except (ValueError, TypeError) as e:
            return {"ok": False, "provider": "openai", "model": model, "error": "invalid_base64", "detail": str(e)[:_DETAIL_MAX]}
    else:
        img_url = first.get("url")
        if not isinstance(img_url, str) or not img_url.startswith("http"):
            return {"ok": False, "provider": "openai", "model": model, "error": "no_image", "detail": str(data)[:_DETAIL_MAX]}
        try:
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                ir = client.get(img_url)
        except httpx.HTTPError as e:
            return {"ok": False, "provider": "openai", "model": model, "error": "image_download_http_error", "detail": str(e)[:_DETAIL_MAX]}
        if ir.status_code >= 400:
            return {"ok": False, "provider": "openai", "model": model, "error": f"download_http_{ir.status_code}", "detail": img_url[:256]}
        raw = ir.content
    if not raw or len(raw) < 32:
        return {"ok": False, "provider": "openai", "model": model, "error": "empty_or_tiny_image", "detail": f"len={len(raw or b'')}"}
    return {"ok": True, "provider": "openai", "model": model, "bytes": raw, "content_type": "image/png"}
