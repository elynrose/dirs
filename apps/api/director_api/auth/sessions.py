"""Opaque server sessions (Redis) for HttpOnly cookie auth."""

from __future__ import annotations

import json
import secrets
from typing import Any

import structlog

from director_api.config import Settings, get_settings
from director_api.infra.redis_client import get_redis_client

log = structlog.get_logger(__name__)

_SESSION_PREFIX = "director:sess:"


def _key(session_id: str) -> str:
    return f"{_SESSION_PREFIX}{session_id}"


def create_server_session(*, user_id: int, tenant_id: str) -> str | None:
    """Create a new opaque session. Returns None if Redis is unavailable."""
    r = get_redis_client()
    if r is None:
        return None
    settings = get_settings()
    sid = secrets.token_urlsafe(32)
    payload = {"user_id": int(user_id), "tenant_id": str(tenant_id).strip()}
    ttl = int(settings.director_session_ttl_seconds)
    try:
        r.set(_key(sid), json.dumps(payload), ex=ttl)
    except Exception as exc:
        log.warning("session_create_failed", error=str(exc)[:200])
        return None
    return sid


def get_server_session(session_id: str) -> dict[str, Any] | None:
    """Return ``{"user_id": int, "tenant_id": str}`` or None if missing/expired."""
    r = get_redis_client()
    if r is None:
        return None
    try:
        raw = r.get(_key(session_id))
    except Exception as exc:
        log.warning("session_get_failed", error=str(exc)[:200])
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        uid = int(data["user_id"])
        tid = str(data.get("tenant_id") or "").strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not tid:
        return None
    return {"user_id": uid, "tenant_id": tid}


def delete_server_session(session_id: str) -> None:
    r = get_redis_client()
    if r is None:
        return
    try:
        r.delete(_key(session_id))
    except Exception as exc:
        log.warning("session_delete_failed", error=str(exc)[:200])


def touch_server_session(session_id: str, *, tenant_id: str | None = None) -> bool:
    """Refresh TTL and optionally update active workspace id in the session blob."""
    r = get_redis_client()
    if r is None:
        return False
    settings = get_settings()
    ttl = int(settings.director_session_ttl_seconds)
    cur = get_server_session(session_id)
    if cur is None:
        return False
    if tenant_id is not None and str(tenant_id).strip():
        cur["tenant_id"] = str(tenant_id).strip()
    try:
        pipe = r.pipeline(transaction=False)
        pipe.set(_key(session_id), json.dumps(cur), ex=ttl)
        pipe.execute()
    except Exception as exc:
        log.warning("session_touch_failed", error=str(exc)[:200])
        return False
    return True


def _samesite_literal(settings: Settings) -> str:
    v = (settings.director_session_cookie_samesite or "lax").strip().lower()
    if v not in ("lax", "strict", "none"):
        return "lax"
    return v


def attach_session_cookie(response: Any, session_id: str, *, settings: Settings | None = None) -> None:
    """Set HttpOnly session cookie on a Starlette/FastAPI response."""
    s = settings or get_settings()
    ttl = int(s.director_session_ttl_seconds)
    response.set_cookie(
        key=s.director_session_cookie_name,
        value=session_id,
        max_age=ttl,
        httponly=True,
        secure=bool(s.director_session_cookie_secure),
        samesite=_samesite_literal(s),
        path="/",
    )


def clear_session_cookie(response: Any, *, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    response.delete_cookie(s.director_session_cookie_name, path="/")


def looks_like_jwt(token: str) -> bool:
    """Heuristic: HS256 access tokens are three base64url segments."""
    t = (token or "").strip()
    return t.count(".") == 2
