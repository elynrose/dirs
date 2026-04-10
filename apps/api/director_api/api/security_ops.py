"""Guard operational routes (metrics, worker restart) when multi-tenant auth is enabled."""

from __future__ import annotations

from fastapi import HTTPException, Request

from director_api.config import get_settings


def assert_ops_route_allowed(request: Request) -> None:
    """When DIRECTOR_AUTH_ENABLED is true, require X-Director-Ops-Key matching DIRECTOR_OPS_API_KEY.

    If the ops key is not configured, the route is not exposed (404) so hosted deployments
    do not leak metrics or remote worker control.
    """
    settings = get_settings()
    if not settings.director_auth_enabled:
        return
    expected = (settings.director_ops_api_key or "").strip()
    if not expected:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    got = (request.headers.get("x-director-ops-key") or request.headers.get("X-Director-Ops-Key") or "").strip()
    if got != expected:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
