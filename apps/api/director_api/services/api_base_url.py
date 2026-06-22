"""Resolve API base URLs for OAuth redirects (local dev vs public production)."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

OAuthRedirectBaseMode = Literal["auto", "local", "public", "request"]

YOUTUBE_OAUTH_CALLBACK_PATH = "/v1/integrations/youtube/oauth-callback"


def normalize_api_base_url(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().rstrip("/")
    if not s:
        return None
    # Guard against mis-set env that pasted the full callback path.
    suffix = YOUTUBE_OAUTH_CALLBACK_PATH
    while s.endswith(suffix):
        s = s[: -len(suffix)].rstrip("/")
    return s or None


def is_loopback_host(host: str | None) -> bool:
    h = (host or "").strip().lower()
    if h in ("localhost", "127.0.0.1", "::1"):
        return True
    if h.startswith("127."):
        return True
    return False


def _mode_from_settings(settings: Any) -> OAuthRedirectBaseMode:
    raw = str(getattr(settings, "oauth_redirect_base", None) or "auto").strip().lower()
    if raw in ("auto", "local", "public", "request"):
        return raw  # type: ignore[return-value]
    return "auto"


def resolve_oauth_api_base_url(
    settings: Any,
    *,
    request_host: str | None = None,
    request_base_url: str | None = None,
) -> str | None:
    """
    Pick the API base (no trailing slash) used to build YouTube OAuth redirect_uri.

    ``auto`` (default): loopback request → ``local_api_base_url``; else ``public_api_base_url``;
    then fall back to the other, then incoming request base URL.
    """
    local = normalize_api_base_url(getattr(settings, "local_api_base_url", None))
    public = normalize_api_base_url(getattr(settings, "public_api_base_url", None))
    mode = _mode_from_settings(settings)

    if mode == "local":
        return local or public or normalize_api_base_url(request_base_url)
    if mode == "public":
        return public or local or normalize_api_base_url(request_base_url)
    if mode == "request":
        return normalize_api_base_url(request_base_url)

    loopback = is_loopback_host(request_host)
    if loopback and local:
        return local
    if public:
        return public
    if local:
        return local
    return normalize_api_base_url(request_base_url)


def youtube_oauth_redirect_uri(
    settings: Any,
    *,
    request_host: str | None = None,
    request_base_url: str | None = None,
    fallback_callback_url: str | None = None,
) -> str:
    """Full redirect URI registered in Google Cloud Console."""
    base = resolve_oauth_api_base_url(
        settings,
        request_host=request_host,
        request_base_url=request_base_url,
    )
    if base:
        return f"{base}{YOUTUBE_OAUTH_CALLBACK_PATH}"
    if fallback_callback_url:
        return fallback_callback_url.strip()
    return YOUTUBE_OAUTH_CALLBACK_PATH


def oauth_redirect_uri_candidates(
    settings: Any,
    *,
    request_host: str | None = None,
    request_base_url: str | None = None,
    fallback_callback_url: str | None = None,
) -> dict[str, str | None]:
    """Local/public/active redirect URIs for Studio status display."""
    local_base = normalize_api_base_url(getattr(settings, "local_api_base_url", None))
    public_base = normalize_api_base_url(getattr(settings, "public_api_base_url", None))
    active = resolve_oauth_api_base_url(
        settings,
        request_host=request_host,
        request_base_url=request_base_url,
    )
    active_uri = youtube_oauth_redirect_uri(
        settings,
        request_host=request_host,
        request_base_url=request_base_url,
        fallback_callback_url=fallback_callback_url,
    )
    return {
        "oauth_redirect_base_mode": _mode_from_settings(settings),
        "oauth_redirect_uri_active": active_uri,
        "oauth_redirect_uri_local": (
            f"{local_base}{YOUTUBE_OAUTH_CALLBACK_PATH}" if local_base else None
        ),
        "oauth_redirect_uri_public": (
            f"{public_base}{YOUTUBE_OAUTH_CALLBACK_PATH}" if public_base else None
        ),
        "oauth_api_base_active": active,
    }


def request_host_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).hostname
    except ValueError:
        return None
