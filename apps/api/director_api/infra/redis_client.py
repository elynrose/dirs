"""Shared Redis client for API features (rate limit, sessions, etc.)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from director_api.config import get_settings

if TYPE_CHECKING:
    import redis as redis_lib

log = structlog.get_logger(__name__)

_client: "redis_lib.Redis | None" = None
_unavailable: bool = False


def close_redis_client() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None


def get_redis_client() -> "redis_lib.Redis | None":
    """Return a decode_responses=True Redis client, or None if Redis is down."""
    global _client, _unavailable
    if _client is not None:
        try:
            _client.ping()
            return _client
        except Exception as exc:
            log.warning("redis_client_stale", error=str(exc)[:200])
            close_redis_client()
            _unavailable = False
    try:
        import redis

        settings = get_settings()
        _client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        _client.ping()
        _unavailable = False
        return _client
    except Exception as exc:
        if not _unavailable:
            log.warning("redis_client_unavailable", error=str(exc))
            _unavailable = True
        return None
