"""Reject oversized HTTP request bodies using Content-Length (cheap DoS guard)."""

from __future__ import annotations

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from director_api.config import get_settings

log = structlog.get_logger(__name__)

_SKIP_PREFIXES = (
    "/v1/health",
    "/v1/ready",
    "/v1/metrics",
)


class LimitRequestBodyMiddleware(BaseHTTPMiddleware):
    """Return 413 when Content-Length exceeds configured ``api_max_request_body_bytes``.

    Does not inspect chunked bodies without Content-Length (rare for our clients).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)
        if request.method in ("GET", "HEAD", "OPTIONS", "DELETE"):
            return await call_next(request)
        cl = request.headers.get("content-length")
        if not cl:
            return await call_next(request)
        try:
            n = int(cl)
        except ValueError:
            return await call_next(request)
        max_b = int(get_settings().api_max_request_body_bytes)
        if n > max_b:
            log.warning("request_body_too_large", path=path, content_length=n, max_bytes=max_b)
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": f"request body exceeds limit of {max_b} bytes",
                    }
                },
            )
        return await call_next(request)
