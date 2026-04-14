"""YouTube Data API v3: OAuth refresh + resumable video upload (httpx, no google client libs)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

log = structlog.get_logger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_UPLOAD_INIT = "https://www.googleapis.com/upload/youtube/v3/videos"


def sign_youtube_oauth_state(tenant_id: str, secret: str) -> str:
    payload = {"tid": (tenant_id or "").strip(), "exp": int(time.time()) + 900}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), b.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{b}.{sig}"


def verify_youtube_oauth_state(state: str, secret: str) -> str | None:
    parts = (state or "").split(".", 1)
    if len(parts) != 2:
        return None
    b, sig = parts[0], parts[1]
    if hmac.new(secret.encode("utf-8"), b.encode("utf-8"), hashlib.sha256).hexdigest()[:32] != sig:
        return None
    pad = "=" * (-len(b) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(b + pad))
    except (json.JSONDecodeError, ValueError):
        return None
    if int(data.get("exp") or 0) < time.time():
        return None
    tid = str(data.get("tid") or "").strip()
    return tid or None


def exchange_authorization_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    body = {
        "code": code.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "redirect_uri": redirect_uri.strip(),
        "grant_type": "authorization_code",
    }
    r = httpx.post(GOOGLE_TOKEN_URL, data=body, timeout=timeout_sec)
    r.raise_for_status()
    return r.json()


def refresh_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    timeout_sec: float = 60.0,
) -> str:
    body = {
        "refresh_token": refresh_token.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "grant_type": "refresh_token",
    }
    r = httpx.post(GOOGLE_TOKEN_URL, data=body, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()
    tok = str(data.get("access_token") or "").strip()
    if not tok:
        raise RuntimeError("no access_token in refresh response")
    return tok


def build_google_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scope: str = "https://www.googleapis.com/auth/youtube.upload",
) -> str:
    q = {
        "client_id": client_id.strip(),
        "redirect_uri": redirect_uri.strip(),
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(q)}"


def upload_mp4_resumable(
    *,
    access_token: str,
    file_path: Path,
    title: str,
    description: str = "",
    privacy_status: str = "unlisted",
    timeout_sec: float = 7200.0,
) -> dict[str, Any]:
    """Single-request resumable upload (file must fit in memory for one PUT)."""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    data = path.read_bytes()
    size = len(data)
    meta = {
        "snippet": {"title": (title or path.stem)[:100], "description": (description or "")[:5000]},
        "status": {"privacyStatus": privacy_status if privacy_status in ("public", "unlisted", "private") else "unlisted"},
    }
    init_url = f"{YOUTUBE_UPLOAD_INIT}?uploadType=resumable&part=snippet,status"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(size),
        "X-Upload-Content-Type": "video/mp4",
    }
    with httpx.Client(timeout=timeout_sec) as client:
        r1 = client.post(init_url, headers=headers, json=meta)
        r1.raise_for_status()
        upload_url = r1.headers.get("location") or r1.headers.get("Location")
        if not upload_url:
            raise RuntimeError("YouTube init response missing Location header")
        r2 = client.put(
            upload_url,
            content=data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(size),
            },
        )
        r2.raise_for_status()
        out = r2.json()
    return out


def watch_url_from_upload_response(resp: dict[str, Any]) -> str | None:
    vid = str((resp or {}).get("id") or "").strip()
    if not vid:
        return None
    return f"https://www.youtube.com/watch?v={vid}"


def share_url_from_upload_response(resp: dict[str, Any]) -> str | None:
    """Short share link when available (same as watch for uploads)."""
    return watch_url_from_upload_response(resp)
