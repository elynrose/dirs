"""Registration and login when ``DIRECTOR_AUTH_ENABLED=true``."""

from __future__ import annotations

import uuid
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.auth.deps import extract_token
from director_api.auth.jwtutil import decode_access_token, issue_access_token
from director_api.auth.passwords import hash_password, verify_password
from director_api.config import get_settings
from director_api.db.models import Tenant, TenantMembership, User
from director_api.db.session import get_db
from director_api.services.billing_plans_seed import assign_free_plan_to_new_tenant
from director_api.services.firebase_id_token import (
    firebase_public_web_config,
    firebase_sign_in_available,
    verify_firebase_id_token,
)

router = APIRouter(tags=["auth"])


@router.get("/auth/config")
def auth_config() -> dict[str, Any]:
    """Public: whether the API expects JWT + X-Tenant-Id (Studio bootstraps from this)."""
    s = get_settings()
    data: dict[str, Any] = {
        "auth_enabled": bool(s.director_auth_enabled),
        "allow_registration": bool(s.director_allow_registration),
    }
    fb = firebase_public_web_config(s)
    if fb:
        data["firebase"] = fb
    return {"data": data, "meta": {}}


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    tenant_name: str = Field(min_length=1, max_length=256)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class FirebaseSignInIn(BaseModel):
    """Exchange a Firebase Auth ID token (Google, etc.) for a Directely JWT."""

    id_token: str = Field(min_length=20, max_length=16_384)
    tenant_name: str = Field(default="My workspace", min_length=1, max_length=256)


class TenantOut(BaseModel):
    id: str
    name: str
    role: str


class AuthOkOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: str
    tenants: list[TenantOut]
    email: str


def _tenant_rows_for_user(db: Session, user_id: int) -> list[TenantMembership]:
    return list(
        db.scalars(
            select(TenantMembership).where(TenantMembership.user_id == user_id).order_by(TenantMembership.created_at)
        ).all()
    )


def _user_profile_public(u: User) -> dict[str, Any]:
    return {
        "user_id": str(u.id),
        "email": u.email,
        "full_name": (u.full_name or "").strip() or None,
        "city": (u.city or "").strip() or None,
        "state": (u.state or "").strip() or None,
        "country": (u.country or "").strip() or None,
        "zip_code": (u.zip_code or "").strip() or None,
    }


def _require_user(request: Request, db: Session) -> User:
    """Bearer or session cookie; same rules as GET /auth/me."""
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    token = extract_token(request, settings)
    if not token:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "missing credentials"})
    try:
        claims = decode_access_token(settings, token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "invalid or expired token"})
    try:
        user_id = int(str(claims["sub"]).strip())
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "invalid token subject"})
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "user not found"})
    return user


def _serialize_tenants(db: Session, memberships: list[TenantMembership]) -> list[TenantOut]:
    out: list[TenantOut] = []
    for m in memberships:
        t = db.get(Tenant, m.tenant_id)
        name = t.name if t else m.tenant_id
        out.append(TenantOut(id=m.tenant_id, name=name, role=m.role))
    return out


@router.post("/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})

    n_users = int(db.scalar(select(func.count()).select_from(User)) or 0)
    if not settings.director_allow_registration and n_users > 0:
        raise HTTPException(
            status_code=403,
            detail={"code": "REGISTRATION_CLOSED", "message": "registration is disabled"},
        )

    existing = db.scalar(select(User).where(User.email == body.email.lower()))
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"code": "EMAIL_IN_USE", "message": "email already registered"},
        )

    tid = str(uuid.uuid4())
    tenant = Tenant(id=tid, name=body.tenant_name.strip(), slug=None)
    user = User(
        email=body.email.lower().strip(),
        password_hash=hash_password(body.password),
    )
    db.add(tenant)
    db.add(user)
    # Assign user PK before tenant_memberships (user_id is BIGINT identity).
    db.flush()
    mem = TenantMembership(
        id=uuid.uuid4(),
        user_id=user.id,
        tenant_id=tid,
        role="owner",
    )
    db.add(mem)
    assign_free_plan_to_new_tenant(db, tid, settings)
    db.commit()

    token = issue_access_token(settings=settings, user_id=user.id, tenant_id=tid)
    tenants = _serialize_tenants(db, [mem])
    return {
        "data": AuthOkOut(
            access_token=token,
            tenant_id=tid,
            tenants=tenants,
            email=user.email,
        ).model_dump(mode="json"),
        "meta": {},
    }


@router.post("/auth/firebase")
def auth_firebase(body: FirebaseSignInIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Verify a Firebase ID token and return the same payload as email/password login."""
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    if not firebase_sign_in_available(settings):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})

    try:
        claims = verify_firebase_id_token(settings, body.id_token.strip())
    except Exception:
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_FIREBASE_TOKEN", "message": "invalid or expired firebase credential"},
        )

    uid = str(claims.get("uid") or "").strip()
    if not uid:
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_FIREBASE_TOKEN", "message": "token missing uid"},
        )
    email_raw = claims.get("email")
    email = str(email_raw).strip().lower() if email_raw else ""
    if not email:
        raise HTTPException(
            status_code=400,
            detail={"code": "EMAIL_REQUIRED", "message": "firebase user has no email"},
        )

    user_by_uid = db.scalar(select(User).where(User.firebase_uid == uid))
    user_by_email = db.scalar(select(User).where(User.email == email))
    if user_by_uid and user_by_email and user_by_uid.id != user_by_email.id:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDENTITY_CONFLICT", "message": "email and firebase identity do not match"},
        )

    user = user_by_uid or user_by_email
    if user:
        if user_by_email and not user_by_uid and user.firebase_uid and user.firebase_uid != uid:
            raise HTTPException(
                status_code=409,
                detail={"code": "IDENTITY_CONFLICT", "message": "this email is linked to a different sign-in"},
            )
        user.firebase_uid = uid
        nm = claims.get("name")
        if nm and not (user.full_name or "").strip():
            user.full_name = str(nm).strip()[:256] or None
        db.commit()
        db.refresh(user)
    else:
        n_users = int(db.scalar(select(func.count()).select_from(User)) or 0)
        if not settings.director_allow_registration and n_users > 0:
            raise HTTPException(
                status_code=403,
                detail={"code": "REGISTRATION_CLOSED", "message": "registration is disabled"},
            )
        tid = str(uuid.uuid4())
        tenant_name = body.tenant_name.strip() if body.tenant_name.strip() else "My workspace"
        tenant = Tenant(id=tid, name=tenant_name[:256], slug=None)
        nm = claims.get("name")
        user = User(
            email=email,
            password_hash=None,
            firebase_uid=uid,
            full_name=str(nm).strip()[:256] if nm else None,
        )
        db.add(tenant)
        db.add(user)
        db.flush()
        mem = TenantMembership(
            id=uuid.uuid4(),
            user_id=user.id,
            tenant_id=tid,
            role="owner",
        )
        db.add(mem)
        assign_free_plan_to_new_tenant(db, tid, settings)
        db.commit()
        db.refresh(user)

    memberships = _tenant_rows_for_user(db, user.id)
    if not memberships:
        raise HTTPException(
            status_code=403,
            detail={"code": "NO_WORKSPACE", "message": "user has no workspace membership"},
        )
    default_tid = memberships[0].tenant_id
    token = issue_access_token(settings=settings, user_id=user.id, tenant_id=default_tid)
    tenants = _serialize_tenants(db, memberships)
    return {
        "data": AuthOkOut(
            access_token=token,
            tenant_id=default_tid,
            tenants=tenants,
            email=user.email,
        ).model_dump(mode="json"),
        "meta": {},
    }


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})

    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_CREDENTIALS", "message": "invalid email or password"},
        )

    memberships = _tenant_rows_for_user(db, user.id)
    if not memberships:
        raise HTTPException(
            status_code=403,
            detail={"code": "NO_WORKSPACE", "message": "user has no workspace membership"},
        )
    default_tid = memberships[0].tenant_id
    token = issue_access_token(settings=settings, user_id=user.id, tenant_id=default_tid)
    tenants = _serialize_tenants(db, memberships)
    return {
        "data": AuthOkOut(
            access_token=token,
            tenant_id=default_tid,
            tenants=tenants,
            email=user.email,
        ).model_dump(mode="json"),
        "meta": {},
    }


@router.post("/auth/logout")
def logout(response: Response) -> dict[str, Any]:
    settings = get_settings()
    if settings.director_auth_enabled:
        response.delete_cookie(settings.director_session_cookie_name, path="/")
    return {"data": {"ok": True}, "meta": {}}


@router.post("/auth/refresh")
def auth_refresh(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Issue a new JWT for the current user and workspace. Call while the token is still valid if ``director_jwt_expire_hours`` is short."""
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    user = _require_user(request, db)
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
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == tid,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "not a member of this workspace"},
        )
    token = issue_access_token(settings=settings, user_id=user.id, tenant_id=tid)
    tenants = _serialize_tenants(db, _tenant_rows_for_user(db, user.id))
    return {
        "data": {
            "access_token": token,
            "tenant_id": tid,
            "tenants": [t.model_dump(mode="json") for t in tenants],
            "email": user.email,
        },
        "meta": {},
    }


@router.get("/auth/me")
def auth_me(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return session info and all workspaces (valid Bearer or session cookie; no X-Tenant-Id)."""
    settings = get_settings()
    if not settings.director_auth_enabled:
        return {
            "data": {
                "auth_enabled": False,
                "default_tenant_id": settings.default_tenant_id,
                "user_id": None,
                "email": None,
                "full_name": None,
                "city": None,
                "state": None,
                "country": None,
                "zip_code": None,
                "entitlements": {
                    "chat_enabled": True,
                    "telegram_enabled": True,
                    "max_projects": None,
                    "full_through_automation_enabled": True,
                    "hands_off_unattended_enabled": True,
                    "subtitles_enabled": True,
                },
                "billing": {
                    "status": "none",
                    "plan_slug": None,
                    "plan_display_name": None,
                    "current_period_end": None,
                    "days_remaining_in_period": None,
                },
            },
            "meta": {},
        }

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

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "user not found"})

    memberships = _tenant_rows_for_user(db, user.id)
    tenants = _serialize_tenants(db, memberships)
    default_tid = memberships[0].tenant_id if memberships else None
    tid_header = (request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-Id") or "").strip()
    active_tid = default_tid
    if tid_header and any(m.tenant_id == tid_header for m in memberships):
        active_tid = tid_header

    from director_api.services.tenant_entitlements import billing_summary_for_tenant, get_effective_entitlements

    entitlements: dict[str, Any] = {}
    billing: dict[str, Any] = {"status": "none"}
    if active_tid:
        entitlements = get_effective_entitlements(db, active_tid, auth_enabled=True)
        billing = billing_summary_for_tenant(db, active_tid)

    data = {
        "auth_enabled": True,
        "tenant_id": default_tid,
        "active_tenant_id": active_tid,
        "tenants": [t.model_dump(mode="json") for t in tenants],
        "entitlements": entitlements,
        "billing": billing,
    }
    data.update(_user_profile_public(user))
    return {"data": data, "meta": {}}


class AuthProfilePatch(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(None, max_length=256)
    city: str | None = Field(None, max_length=128)
    state: str | None = Field(None, max_length=128)
    country: str | None = Field(None, max_length=128)
    zip_code: str | None = Field(None, max_length=32)


class AuthPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


@router.patch("/auth/me")
def auth_patch_me(request: Request, body: AuthProfilePatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Update signed-in user profile and/or email. Only fields present in the JSON body are applied."""
    user = _require_user(request, db)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        return {"data": _user_profile_public(user), "meta": {}}
    if "email" in patch:
        em = patch["email"].lower().strip()
        existing = db.scalar(select(User).where(User.email == em, User.id != user.id))
        if existing:
            raise HTTPException(status_code=409, detail={"code": "EMAIL_IN_USE", "message": "email already in use"})
        user.email = em
    if "full_name" in patch:
        user.full_name = (patch["full_name"] or "").strip() or None
    if "city" in patch:
        user.city = (patch["city"] or "").strip() or None
    if "state" in patch:
        user.state = (patch["state"] or "").strip() or None
    if "country" in patch:
        user.country = (patch["country"] or "").strip() or None
    if "zip_code" in patch:
        user.zip_code = (patch["zip_code"] or "").strip() or None
    db.commit()
    db.refresh(user)
    return {"data": _user_profile_public(user), "meta": {}}


@router.post("/auth/change-password")
def auth_change_password(
    request: Request, body: AuthPasswordChange, db: Session = Depends(get_db)
) -> dict[str, Any]:
    user = _require_user(request, db)
    if not user.password_hash or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_PASSWORD", "message": "current password is incorrect"},
        )
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"data": {"ok": True}, "meta": {}}
