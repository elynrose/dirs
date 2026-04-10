"""Platform admin routes: require ``X-Director-Admin-Key`` matching ``DIRECTOR_ADMIN_API_KEY``."""

from __future__ import annotations

from fastapi import HTTPException, Request

from director_api.config import get_settings


def assert_admin_request(request: Request) -> None:
    """Reject if admin key is not configured or header does not match."""
    settings = get_settings()
    expected = (settings.director_admin_api_key or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ADMIN_NOT_CONFIGURED",
                "message": "Set DIRECTOR_ADMIN_API_KEY in the environment to use the admin API",
            },
        )
    got = (request.headers.get("x-director-admin-key") or request.headers.get("X-Director-Admin-Key") or "").strip()
    if got != expected:
        raise HTTPException(
            status_code=401,
            detail={"code": "ADMIN_UNAUTHORIZED", "message": "invalid or missing admin key"},
        )
