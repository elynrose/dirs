import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from director_api.middleware.tenant_hint import resolve_request_log_tenant_id


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Binds request_id, tenant_id, and log fields per `docs/phase-6-telemetry-fields.md`."""

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        tenant = resolve_request_log_tenant_id(request)
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
