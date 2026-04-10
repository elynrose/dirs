from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.auth.context import AuthContext
from director_api.auth.jwtutil import decode_access_token
from director_api.config import Settings, get_settings
from director_api.db.models import TenantMembership
from director_api.db.session import get_db


def extract_token(request: Request, settings: Settings | None = None) -> str | None:
    q = request.query_params.get("access_token")
    if isinstance(q, str) and q.strip():
        return q.strip()
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if h and h.lower().startswith("bearer "):
        return h[7:].strip()
    s = settings or get_settings()
    return request.cookies.get(s.director_session_cookie_name)


def auth_context_dep(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    settings = get_settings()
    if not settings.director_auth_enabled:
        return AuthContext(
            tenant_id=settings.default_tenant_id,
            user_id=None,
            role="owner",
        )

    token = extract_token(request, settings)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "missing credentials"},
        )
    try:
        claims = decode_access_token(settings, token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "invalid or expired token"},
        )
    try:
        user_id = int(str(claims["sub"]).strip())
    except (KeyError, ValueError, TypeError):
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "invalid token subject"},
        )

    # Tenant for authorization remains header/query-scoped so users can switch workspaces without
    # a fresh token; signed ``tid`` in the JWT is used for rate limiting (see middleware).
    tid = (
        (request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-Id") or "").strip()
        or (request.query_params.get("tenant_id") or "").strip()
    )
    if not tid:
        raise HTTPException(
            status_code=400,
            detail={"code": "TENANT_REQUIRED", "message": "X-Tenant-Id header is required"},
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
    return AuthContext(tenant_id=tid, user_id=str(user_id), role=row.role)


AuthContextDep = Annotated[AuthContext, Depends(auth_context_dep)]
