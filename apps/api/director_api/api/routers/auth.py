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
from director_api.auth.sessions import (
    attach_session_cookie,
    clear_session_cookie,
    create_server_session,
    delete_server_session,
    get_server_session,
    looks_like_jwt,
    touch_server_session,
)
from director_api.auth.passwords import hash_password, verify_password
from director_api.config import Settings, get_settings
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


class SessionTenantIn(BaseModel):
    """Switch active workspace for the opaque Redis-backed browser session."""

    tenant_id: str = Field(min_length=1, max_length=256)


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


def authenticate_user_from_request(request: Request, db: Session) -> User:
    """Resolve the signed-in user from Bearer JWT or opaque ``director_session`` cookie."""
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    token = extract_token(request, settings)
    if not token:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "missing credentials"})
    if looks_like_jwt(token):
        try:
            claims = decode_access_token(settings, token)
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "invalid or expired token"})
        try:
            user_id = int(str(claims["sub"]).strip())
        except (KeyError, ValueError, TypeError):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "invalid token subject"})
    else:
        sess = get_server_session(token)
        if not sess:
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "invalid or expired session"})
        try:
            user_id = int(sess["user_id"])
        except (KeyError, ValueError, TypeError):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "invalid session subject"})
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "user not found"})
    return user


def _require_user(request: Request, db: Session) -> User:
    """Bearer or session cookie; same rules as GET /auth/me."""
    return authenticate_user_from_request(request, db)


def _serialize_tenants(db: Session, memberships: list[TenantMembership]) -> list[TenantOut]:
    out: list[TenantOut] = []
    for m in memberships:
        t = db.get(Tenant, m.tenant_id)
        name = t.name if t else m.tenant_id
        out.append(TenantOut(id=m.tenant_id, name=name, role=m.role))
    return out


def _create_web_session_and_token(
    *,
    response: Response,
    settings: Settings,
    db: Session,
    user: User,
    default_tid: str,
    memberships: list[TenantMembership],
) -> AuthOkOut:
    """HttpOnly opaque session (Redis) plus a signed JWT for media query strings and CLI clients."""
    sid = create_server_session(user_id=user.id, tenant_id=default_tid)
    if not sid:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SESSION_STORE_UNAVAILABLE",
                "message": "login requires Redis; check REDIS_URL or try again",
            },
        )
    attach_session_cookie(response, sid, settings=settings)
    token = issue_access_token(settings=settings, user_id=user.id, tenant_id=default_tid)
    tenants = _serialize_tenants(db, memberships)
    return AuthOkOut(
        access_token=token,
        tenant_id=default_tid,
        tenants=tenants,
        email=user.email,
    )


def _ensure_user_has_workspace_membership(db: Session, user: User, settings: Settings) -> None:
    """Create a default workspace + owner membership when the user has none (admin-created users, imports, etc.)."""
    if _tenant_rows_for_user(db, user.id):
        return
    tid = str(uuid.uuid4())
    label = "My workspace"
    fn = (user.full_name or "").strip()
    if fn:
        first = fn.split()[0]
        if first:
            label = f"{first}'s workspace"[:256]
    tenant = Tenant(id=tid, name=label, slug=None)
    mem = TenantMembership(
        id=uuid.uuid4(),
        user_id=user.id,
        tenant_id=tid,
        role="owner",
    )
    db.add(tenant)
    db.add(mem)
    db.flush()
    assign_free_plan_to_new_tenant(db, tid, settings)
    db.commit()


@router.post("/auth/register")
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
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

    tenants = _serialize_tenants(db, [mem])
    ok = _create_web_session_and_token(
        response=response,
        settings=settings,
        db=db,
        user=user,
        default_tid=tid,
        memberships=[mem],
    )
    return {"data": ok.model_dump(mode="json"), "meta": {}}


@router.post("/auth/firebase")
def auth_firebase(body: FirebaseSignInIn, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
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

    _ensure_user_has_workspace_membership(db, user, settings)
    memberships = _tenant_rows_for_user(db, user.id)
    if not memberships:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "WORKSPACE_PROVISION_FAILED",
                "message": "could not create a workspace; try again or contact support",
            },
        )
    default_tid = memberships[0].tenant_id
    ok = _create_web_session_and_token(
        response=response,
        settings=settings,
        db=db,
        user=user,
        default_tid=default_tid,
        memberships=memberships,
    )
    return {"data": ok.model_dump(mode="json"), "meta": {}}


@router.post("/auth/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})

    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_CREDENTIALS", "message": "invalid email or password"},
        )

    _ensure_user_has_workspace_membership(db, user, settings)
    memberships = _tenant_rows_for_user(db, user.id)
    if not memberships:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "WORKSPACE_PROVISION_FAILED",
                "message": "could not create a workspace; try again or contact support",
            },
        )
    default_tid = memberships[0].tenant_id
    ok = _create_web_session_and_token(
        response=response,
        settings=settings,
        db=db,
        user=user,
        default_tid=default_tid,
        memberships=memberships,
    )
    return {"data": ok.model_dump(mode="json"), "meta": {}}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    settings = get_settings()
    if settings.director_auth_enabled:
        tok = extract_token(request, settings)
        if tok and not looks_like_jwt(tok):
            delete_server_session(tok)
        clear_session_cookie(response, settings=settings)
    return {"data": {"ok": True}, "meta": {}}


@router.post("/auth/session-tenant")
def auth_session_tenant(
    request: Request,
    response: Response,
    body: SessionTenantIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Update the opaque browser session's active workspace (used before full reload when switching tenants)."""
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    user = authenticate_user_from_request(request, db)
    tok = extract_token(request, settings)
    if not tok or looks_like_jwt(tok):
        raise HTTPException(
            status_code=400,
            detail={"code": "SESSION_COOKIE_REQUIRED", "message": "workspace switch requires a browser session cookie"},
        )
    tid = body.tenant_id.strip()
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
    if not touch_server_session(tok, tenant_id=tid):
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "invalid or expired session"},
        )
    attach_session_cookie(response, tok, settings=settings)
    access = issue_access_token(settings=settings, user_id=user.id, tenant_id=tid)
    tenants = _serialize_tenants(db, _tenant_rows_for_user(db, user.id))
    return {
        "data": {
            "access_token": access,
            "tenant_id": tid,
            "tenants": [t.model_dump(mode="json") for t in tenants],
            "email": user.email,
        },
        "meta": {},
    }


@router.post("/auth/refresh")
def auth_refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Issue a new JWT for the current user and workspace. Call while the token is still valid if ``director_jwt_expire_hours`` is short."""
    settings = get_settings()
    if not settings.director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    user = _require_user(request, db)
    _ensure_user_has_workspace_membership(db, user, settings)
    memberships = _tenant_rows_for_user(db, user.id)
    if not memberships:
        raise HTTPException(
            status_code=503,
            detail={"code": "WORKSPACE_PROVISION_FAILED", "message": "could not create a workspace; try again or contact support"},
        )
    tid = (
        (request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-Id") or "").strip()
        or (request.query_params.get("tenant_id") or "").strip()
    )
    if not tid:
        token_in = extract_token(request, settings)
        try:
            claims = decode_access_token(settings, token_in)
            tid = str(claims.get("tid") or "").strip()
        except jwt.PyJWTError:
            tid = ""
    if not tid or not any(m.tenant_id == tid for m in memberships):
        tid = memberships[0].tenant_id
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
    tok = extract_token(request, settings)
    if tok and not looks_like_jwt(tok):
        touch_server_session(tok, tenant_id=tid)
        attach_session_cookie(response, tok, settings=settings)
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

    user = authenticate_user_from_request(request, db)

    _ensure_user_has_workspace_membership(db, user, settings)
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
