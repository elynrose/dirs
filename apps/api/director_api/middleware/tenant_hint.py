"""Resolve tenant id for logging, rate limits, and middleware (best-effort before auth deps)."""

from __future__ import annotations

from starlette.requests import Request

from director_api.auth.deps import extract_token
from director_api.auth.sessions import get_server_session
from director_api.config import Settings, get_settings


def tenant_id_from_session(request: Request, settings: Settings | None = None) -> str | None:
    """Workspace id from the opaque Redis session when auth is enabled."""
    s = settings or get_settings()
    if not s.director_auth_enabled:
        return None
    token = extract_token(request, s)
    if not token:
        return None
    sess = get_server_session(token)
    if sess:
        tid = str(sess.get("tenant_id") or "").strip()
        if tid:
            return tid
    return None


def resolve_request_log_tenant_id(request: Request) -> str:
    """Tenant for structlog context: session workspace when available, else header, else platform default."""
    settings = get_settings()
    session_tid = tenant_id_from_session(request, settings)
    if session_tid:
        return session_tid
    hdr = (request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-Id") or "").strip()
    if hdr:
        return hdr
    return settings.default_tenant_id
