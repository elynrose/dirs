import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Binds request_id, tenant_id, and log fields per `docs/phase-6-telemetry-fields.md`."""

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        tenant = request.headers.get("x-tenant-id") or "00000000-0000-0000-0000-000000000001"
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=rid,
            tenant_id=tenant,
            http_method=request.method,
            http_path=request.url.path,
        )
        response: Response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        structlog.contextvars.clear_contextvars()
        return response
