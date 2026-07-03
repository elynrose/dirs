"""Google Gemini/Imagen image adapter — sync ``:predict`` (imagen) or ``:generateContent`` (gemini image).

Returns the standard media dict: {ok, bytes?, content_type?, provider, model?, error?, detail?}.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from director_api.config import Settings
from director_api.providers.gemini_rest import GEMINI_API_BASE
from director_api.services.project_frame import coerce_frame_aspect_ratio

_DETAIL_MAX = 2000


def _api_key(settings: Settings) -> str:
    return (getattr(settings, "gemini_api_key", None) or "").strip()


def _aspect(aspect: str | None) -> str:
    return "9:16" if coerce_frame_aspect_ratio(aspect) == "9:16" else "16:9"


def _http_body(text: str) -> str:
    return (text or "").strip()[:_DETAIL_MAX]


def _decode_first(b64: str, model: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError) as e:
        return {"ok": False, "provider": "gemini", "model": model, "error": "invalid_base64", "detail": str(e)[:_DETAIL_MAX]}
    if not raw or len(raw) < 32:
        return {"ok": False, "provider": "gemini", "model": model, "error": "empty_or_tiny_image", "detail": f"len={len(raw or b'')}"}
    return {"ok": True, "provider": "gemini", "model": model, "bytes": raw, "content_type": "image/png"}


def _generate_imagen(settings: Settings, api_key: str, model: str, prompt: str, aspect: str) -> dict[str, Any]:
    url = f"{GEMINI_API_BASE}/models/{model}:predict"
    body: dict[str, Any] = {
        "instances": [{"prompt": prompt[:4000]}],
        "parameters": {"sampleCount": 1, "aspectRatio": aspect},
    }
    try:
        with httpx.Client(timeout=180.0) as client:
            r = client.post(url, params={"key": api_key}, json=body)
    except httpx.HTTPError as e:
        return {"ok": False, "provider": "gemini", "model": model, "error": "http_client_error", "detail": str(e)[:_DETAIL_MAX]}
    if r.status_code >= 400:
        return {"ok": False, "provider": "gemini", "model": model, "error": f"http_{r.status_code}", "detail": _http_body(r.text)}
    try:
        data = r.json()
    except ValueError:
        return {"ok": False, "provider": "gemini", "model": model, "error": "invalid_json", "detail": _http_body(r.text)}
    preds = data.get("predictions") if isinstance(data, dict) else None
    first = preds[0] if isinstance(preds, list) and preds and isinstance(preds[0], dict) else None
    b64 = (first or {}).get("bytesBase64Encoded")
    if not isinstance(b64, str) or not b64:
        return {"ok": False, "provider": "gemini", "model": model, "error": "no_image", "detail": str(data)[:_DETAIL_MAX]}
    return _decode_first(b64, model)


def _generate_gemini_image(settings: Settings, api_key: str, model: str, prompt: str) -> dict[str, Any]:
    """Gemini image models (e.g. gemini-2.x flash image) return inlineData parts via generateContent."""
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt[:4000]}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    try:
        with httpx.Client(timeout=180.0) as client:
            r = client.post(url, params={"key": api_key}, json=body)
    except httpx.HTTPError as e:
        return {"ok": False, "provider": "gemini", "model": model, "error": "http_client_error", "detail": str(e)[:_DETAIL_MAX]}
    if r.status_code >= 400:
        return {"ok": False, "provider": "gemini", "model": model, "error": f"http_{r.status_code}", "detail": _http_body(r.text)}
    try:
        data = r.json()
    except ValueError:
        return {"ok": False, "provider": "gemini", "model": model, "error": "invalid_json", "detail": _http_body(r.text)}
    cands = data.get("candidates") if isinstance(data, dict) else None
    parts = (((cands or [{}])[0] or {}).get("content") or {}).get("parts") or []
    for part in parts:
        if isinstance(part, dict):
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and isinstance(inline.get("data"), str) and inline["data"]:
                return _decode_first(inline["data"], model)
    return {"ok": False, "provider": "gemini", "model": model, "error": "no_image", "detail": str(data)[:_DETAIL_MAX]}


def generate_scene_image(
    settings: Settings,
    prompt: str,
    *,
    model_path: str | None = None,
    negative_prompt: str | None = None,
    frame_aspect_ratio: str | None = None,
) -> dict[str, Any]:
    """Sync image generation via Google. Routes imagen* → :predict, other gemini image models → :generateContent."""
    api_key = _api_key(settings)
    if not api_key:
        return {"ok": False, "provider": "gemini", "error": "GEMINI_API_KEY not set"}
    model = (model_path or getattr(settings, "gemini_image_model", None) or "imagen-4.0-generate-001").strip().lstrip("models/")
    p = (prompt or "").strip()
    neg = (negative_prompt or "").strip()
    if neg:
        p = f"{p}\n\nAvoid: {neg[:1000]}"
    if model.lower().startswith("imagen"):
        return _generate_imagen(settings, api_key, model, p, _aspect(frame_aspect_ratio))
    return _generate_gemini_image(settings, api_key, model, p)
