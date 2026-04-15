"""Redis sliding-window rate limiter (§10.6 baseline: 120 req/min per client IP).

Replaces the previous in-process deque implementation, which counted independently
per Uvicorn worker process and gave each worker its own 120 rpm budget instead of
sharing a single 120 rpm budget across all workers.

Falls back gracefully (allow-through + warning) when Redis is unreachable so a
Redis outage never takes down the API.
"""

from __future__ import annotations

import time
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from director_api.auth.deps import extract_token
from director_api.auth.jwtutil import decode_access_token
from director_api.auth.sessions import get_server_session, looks_like_jwt
from director_api.config import Settings, get_settings
from director_api.infra.redis_client import get_redis_client

log = structlog.get_logger(__name__)

_SKIP_PREFIXES = (
    "/v1/health",
    "/v1/ready",
    "/v1/metrics",
    "/v1/events",
    "/v1/admin",
    "/v1/billing/stripe/webhook",
    "/v1/integrations/telegram",
    "/docs",
    "/openapi",
    "/redoc",
)

def _tenant_id_from_bearer_for_rate_limit(request: Request, settings: Settings) -> str | None:
    """Prefer signed ``tid`` (JWT) or server session default tenant over raw ``X-Tenant-Id``."""
    if not settings.director_auth_enabled:
        return None
    token = extract_token(request, settings)
    if not token:
        return None
    if looks_like_jwt(token):
        try:
            claims = decode_access_token(settings, token)
        except Exception:
            return None
        tid = claims.get("tid")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()
        return None
    sess = get_server_session(token)
    if sess:
        tid = str(sess.get("tenant_id") or "").strip()
        if tid:
            return tid
    return None


class TenantRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter backed by a Redis sorted set.

    Each request appends its wall-clock timestamp to a sorted set keyed by
    ``rl:{ip}:{tenant}``.  Before counting, timestamps older than 60 s are
    pruned.  If the remaining count exceeds ``rpm`` the request is rejected
    with 429.  The set TTL is kept at 120 s to auto-expire idle keys.

    When Redis is unreachable all requests are allowed through and a warning
    is logged once per process lifetime to avoid log spam.
    """

    def __init__(self, app, rpm: int | None = None) -> None:
        super().__init__(app)
        self._rpm = rpm

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        if settings.rate_limit_relax_loopback and client_ip in (
            "127.0.0.1",
            "::1",
            "::ffff:127.0.0.1",
        ):
            return await call_next(request)

        r = get_redis_client()
        if r is None:
            # Redis unavailable — degrade gracefully, allow request through.
            return await call_next(request)

        # Stricter cap for destructive ops endpoint (still requires ops key inside the handler).
        if request.method == "POST" and path.rstrip("/") == "/v1/celery/restart":
            rpm_restart = int(settings.api_celery_restart_rate_limit_per_minute)
            key_restart = f"rl:crestart:{client_ip}"
            now_rs = time.time()
            window_start_rs = now_rs - 60.0
            member_rs = f"{now_rs:.6f}-{id(request)}"
            try:
                pipe_rs = r.pipeline(transaction=False)
                pipe_rs.zremrangebyscore(key_restart, "-inf", window_start_rs)
                pipe_rs.zadd(key_restart, {member_rs: now_rs})
                pipe_rs.zcard(key_restart)
                pipe_rs.expire(key_restart, 120)
                results_rs = pipe_rs.execute()
                count_rs: int = results_rs[2]
            except Exception as exc:
                log.warning("rate_limiter_redis_error", error=str(exc)[:200], client=client_ip)
                return await call_next(request)
            if count_rs > rpm_restart:
                return JSONResponse(
                    status_code=429,
                    content={"error": {"code": "RATE_LIMIT", "message": "too many celery restart requests"}},
                )
            return await call_next(request)

        rpm = self._rpm if self._rpm is not None else int(settings.api_rate_limit_per_minute)
        jwt_tid = _tenant_id_from_bearer_for_rate_limit(request, settings)
        if jwt_tid:
            tenant_hint = jwt_tid
        else:
            tenant_hint = (request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-Id") or "").strip()
        if not tenant_hint:
            tenant_hint = settings.default_tenant_id
        key = f"rl:{client_ip}:{tenant_hint}"
        now = time.time()
        window_start = now - 60.0
        # Use a unique member per request so concurrent timestamps don't collide.
        member = f"{now:.6f}-{id(request)}"

        try:
            pipe = r.pipeline(transaction=False)
            pipe.zremrangebyscore(key, "-inf", window_start)  # prune expired
            pipe.zadd(key, {member: now})                     # record this request
            pipe.zcard(key)                                   # count window
            pipe.expire(key, 120)                             # auto-expire idle keys
            results = pipe.execute()
            count: int = results[2]
        except Exception as exc:
            log.warning("rate_limiter_redis_error", error=str(exc)[:200], client=client_ip)
            return await call_next(request)

        if count > rpm:
            return JSONResponse(
                status_code=429,
                content={"error": {"code": "RATE_LIMIT", "message": "too many requests per minute"}},
            )
        return await call_next(request)
