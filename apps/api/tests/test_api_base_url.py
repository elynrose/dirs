"""Tests for OAuth API base URL resolution."""

from __future__ import annotations

from types import SimpleNamespace

from director_api.services.api_base_url import (
    normalize_api_base_url,
    oauth_redirect_uri_candidates,
    resolve_oauth_api_base_url,
    youtube_oauth_redirect_uri,
)


def _settings(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "local_api_base_url": None,
        "public_api_base_url": None,
        "oauth_redirect_base": "auto",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_normalize_strips_duplicate_callback_path() -> None:
    raw = "https://example.com/v1/integrations/youtube/oauth-callback"
    assert normalize_api_base_url(raw) == "https://example.com"


def test_auto_loopback_prefers_local() -> None:
    s = _settings(
        local_api_base_url="http://127.0.0.1:8000",
        public_api_base_url="https://directely.com",
    )
    assert (
        resolve_oauth_api_base_url(s, request_host="127.0.0.1", request_base_url="http://127.0.0.1:8000")
        == "http://127.0.0.1:8000"
    )


def test_auto_public_host_prefers_public() -> None:
    s = _settings(
        local_api_base_url="http://127.0.0.1:8000",
        public_api_base_url="https://directely.com",
    )
    assert (
        resolve_oauth_api_base_url(s, request_host="directely.com", request_base_url="https://directely.com")
        == "https://directely.com"
    )


def test_force_local_mode() -> None:
    s = _settings(
        oauth_redirect_base="local",
        local_api_base_url="http://127.0.0.1:8000",
        public_api_base_url="https://directely.com",
    )
    assert (
        resolve_oauth_api_base_url(s, request_host="directely.com")
        == "http://127.0.0.1:8000"
    )


def test_youtube_redirect_uri_candidates() -> None:
    s = _settings(
        local_api_base_url="http://127.0.0.1:8000",
        public_api_base_url="https://directely.com",
    )
    info = oauth_redirect_uri_candidates(
        s,
        request_host="127.0.0.1",
        request_base_url="http://127.0.0.1:8000",
    )
    assert info["oauth_redirect_uri_active"] == "http://127.0.0.1:8000/v1/integrations/youtube/oauth-callback"
    assert info["oauth_redirect_uri_local"] == info["oauth_redirect_uri_active"]
    assert info["oauth_redirect_uri_public"] == "https://directely.com/v1/integrations/youtube/oauth-callback"


def test_fallback_callback_url() -> None:
    s = _settings()
    uri = youtube_oauth_redirect_uri(
        s,
        request_host="127.0.0.1",
        fallback_callback_url="http://127.0.0.1:8000/v1/integrations/youtube/oauth-callback",
    )
    assert uri == "http://127.0.0.1:8000/v1/integrations/youtube/oauth-callback"
