"""Platform admin routes: shared secret and/or signed-in workspace administrators.

Either:

- ``X-Director-Admin-Key`` matches ``DIRECTOR_ADMIN_API_KEY`` (automation / legacy), or
- ``Authorization: Bearer`` JWT + ``X-Tenant-Id`` and the user is ``admin`` or ``owner`` in that workspace.
"""

from __future__ import annotations

import jwt
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.auth.deps import extract_token
from director_api.auth.jwtutil import decode_access_token
from director_api.config import get_settings
from director_api.db.models import TenantMembership


def assert_platform_admin_access(request: Request, db: Session) -> None:
    """Require platform admin credentials (shared key or JWT workspace admin)."""
    settings = get_settings()
    expected_key = (settings.director_admin_api_key or "").strip()
    got_key = (request.headers.get("x-director-admin-key") or request.headers.get("X-Director-Admin-Key") or "").strip()

    if expected_key and got_key == expected_key:
        return

    if settings.director_auth_enabled:
        _assert_jwt_workspace_admin(request, db, settings)
        return

    if not expected_key:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ADMIN_NOT_CONFIGURED",
                "message": "Set DIRECTOR_ADMIN_API_KEY in the environment to use the admin API",
            },
        )
    raise HTTPException(
        status_code=401,
        detail={"code": "ADMIN_UNAUTHORIZED", "message": "invalid or missing admin key"},
    )


def _assert_jwt_workspace_admin(request: Request, db: Session, settings) -> None:
    token = extract_token(request, settings)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "ADMIN_UNAUTHORIZED",
                "message": "invalid or missing admin key; or sign in and send Authorization Bearer with X-Tenant-Id",
            },
        )
    try:
        claims = decode_access_token(settings, token)
        user_id = int(str(claims["sub"]).strip())
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=401,
            detail={"code": "ADMIN_UNAUTHORIZED", "message": "invalid or expired token"},
        ) from None
    except (KeyError, ValueError, TypeError):
        raise HTTPException(
            status_code=401,
            detail={"code": "ADMIN_UNAUTHORIZED", "message": "invalid token subject"},
        ) from None

    tid = (request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-Id") or "").strip()
    if not tid:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "TENANT_REQUIRED",
                "message": "X-Tenant-Id header is required for admin API access with Bearer token",
            },
        )

    row = db.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == user_id,
            TenantMembership.tenant_id == tid,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "not a member of this workspace"},
        )
    role = (row.role or "").strip().lower()
    if role not in ("admin", "owner"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FORBIDDEN",
                "message": "workspace admin or owner role required for admin API access",
            },
        )


def assert_admin_request(request: Request) -> None:
    """Legacy entry point without DB (tests only). Prefer ``assert_platform_admin_access``."""
    settings = get_settings()
    expected_key = (settings.director_admin_api_key or "").strip()
    got_key = (request.headers.get("x-director-admin-key") or request.headers.get("X-Director-Admin-Key") or "").strip()
    if expected_key and got_key == expected_key:
        return
    if not expected_key:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ADMIN_NOT_CONFIGURED",
                "message": "Set DIRECTOR_ADMIN_API_KEY in the environment to use the admin API",
            },
        )
    raise HTTPException(
        status_code=401,
        detail={"code": "ADMIN_UNAUTHORIZED", "message": "invalid or missing admin key"},
    )
