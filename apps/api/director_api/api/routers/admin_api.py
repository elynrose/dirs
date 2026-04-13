"""Platform admin API — CRUD for users, tenants, memberships, plans, billing, projects, payment audit.

Auth: header ``X-Director-Admin-Key`` must match ``DIRECTOR_ADMIN_API_KEY``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from director_api.api.routers.agent_runs import _TERMINAL_STATUSES, _handle_agent_run_control
from director_api.api.schemas.agent_run import AgentRunOut, AgentRunPipelineControl
from director_api.api.security_admin import assert_admin_request
from director_api.auth.passwords import hash_password
from director_api.db.models import (
    AgentRun,
    BillingPaymentEvent,
    Job,
    Project,
    SubscriptionPlan,
    Tenant,
    TenantBilling,
    TenantMembership,
    User,
)
from director_api.config import get_settings
from director_api.db.session import get_db
from director_api.services.runtime_settings import invalidate_runtime_settings_cache, resolve_runtime_settings
from director_api.storage.project_storage_cleanup import remove_generated_project_files
from director_api.services.billing_plans_seed import ensure_default_subscription_plans
from director_api.services.platform_stripe_settings import get_or_create_platform_stripe, resolve_effective_stripe_settings
from director_api.services.tenant_entitlements import entitlement_definitions_public

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def admin_auth_dep(request: Request) -> None:
    assert_admin_request(request)


AdminDep = Depends(admin_auth_dep)


def _meta(**extra: Any) -> dict[str, Any]:
    return {k: v for k, v in extra.items() if v is not None}


def _scalar_count_safe(db: Session, table_model: type) -> int:
    """COUNT(*); on missing table or aborted transaction, rollback session and return 0."""
    try:
        return int(db.scalar(select(func.count()).select_from(table_model)) or 0)
    except SQLAlchemyError as e:
        log.warning(
            "admin_scalar_count_failed",
            table=getattr(table_model, "__tablename__", str(table_model)),
            error=str(e),
        )
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return 0


# ---------------------------------------------------------------------------
# Health (verify key)
# ---------------------------------------------------------------------------


@router.get("/health")
def admin_health(_: None = AdminDep) -> dict[str, Any]:
    return {"data": {"ok": True, "role": "platform_admin"}, "meta": {}}


@router.get("/entitlement-definitions")
def admin_entitlement_definitions(_: None = AdminDep) -> dict[str, Any]:
    """Known entitlement keys for admin UI; same keys are stored inside JSONB on plans and overrides."""
    return {"data": {"definitions": entitlement_definitions_public()}, "meta": {}}


class AdminStripeSettingsPatch(BaseModel):
    """Omit a field to leave unchanged; send empty string to clear DB override (fall back to env)."""

    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_publishable_key: str | None = None
    billing_success_url: str | None = None
    billing_cancel_url: str | None = None
    stripe_price_studio_monthly: str | None = None


_STRIPE_PATCH_FIELDS = (
    "stripe_secret_key",
    "stripe_webhook_secret",
    "stripe_publishable_key",
    "billing_success_url",
    "billing_cancel_url",
    "stripe_price_studio_monthly",
)


def _admin_stripe_settings_payload(db: Session) -> dict[str, Any]:
    base = get_settings()
    row = get_or_create_platform_stripe(db)
    eff = resolve_effective_stripe_settings(db, base)

    def _mask_set(v: str | None) -> bool:
        return bool(v and str(v).strip())

    return {
        "effective": {
            "stripe_publishable_key": (eff.get("stripe_publishable_key") or "").strip() or None,
            "billing_success_url": eff.get("billing_success_url") or None,
            "billing_cancel_url": eff.get("billing_cancel_url") or None,
            "stripe_price_studio_monthly": eff.get("stripe_price_studio_monthly"),
        },
        "database_overrides": {
            "stripe_secret_key_set": _mask_set(row.stripe_secret_key),
            "stripe_webhook_secret_set": _mask_set(row.stripe_webhook_secret),
            "stripe_publishable_key": row.stripe_publishable_key,
            "billing_success_url": row.billing_success_url,
            "billing_cancel_url": row.billing_cancel_url,
            "stripe_price_studio_monthly": row.stripe_price_studio_monthly,
        },
    }


@router.get("/stripe-settings")
def admin_get_stripe_settings(db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    """Platform Stripe keys and billing URLs. Secrets are never returned; only whether DB has a value."""
    return {"data": _admin_stripe_settings_payload(db), "meta": {}}


@router.patch("/stripe-settings")
def admin_patch_stripe_settings(
    body: AdminStripeSettingsPatch, db: Session = Depends(get_db), _: None = AdminDep
) -> dict[str, Any]:
    row = get_or_create_platform_stripe(db)
    raw = body.model_dump(exclude_unset=True)
    for k in _STRIPE_PATCH_FIELDS:
        if k not in raw:
            continue
        val = raw[k]
        if val is None:
            setattr(row, k, None)
        elif isinstance(val, str) and not val.strip():
            setattr(row, k, None)
        else:
            setattr(row, k, str(val).strip())
    ensure_default_subscription_plans(db, get_settings())
    db.commit()
    return {"data": {**_admin_stripe_settings_payload(db), "saved": True}, "meta": {}}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard")
def admin_dashboard(db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    nu = _scalar_count_safe(db, User)
    nt = _scalar_count_safe(db, Tenant)
    np = _scalar_count_safe(db, Project)
    nar = _scalar_count_safe(db, AgentRun)
    nj = _scalar_count_safe(db, Job)
    npe = _scalar_count_safe(db, BillingPaymentEvent)
    try:
        active_sub = int(
            db.scalar(
                select(func.count()).select_from(TenantBilling).where(TenantBilling.status.in_(("active", "trialing")))
            )
            or 0
        )
    except SQLAlchemyError as e:
        log.warning("admin_active_sub_count_failed", error=str(e))
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        active_sub = 0
    return {
        "data": {
            "counts": {
                "users": nu,
                "tenants": nt,
                "projects": np,
                "agent_runs": nar,
                "jobs": nj,
                "billing_events": npe,
                "active_subscriptions": active_sub,
            }
        },
        "meta": {},
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def _user_out(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "email": u.email,
        "full_name": (u.full_name or "").strip() or None,
        "city": (u.city or "").strip() or None,
        "state": (u.state or "").strip() or None,
        "country": (u.country or "").strip() or None,
        "zip_code": (u.zip_code or "").strip() or None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


class UserCreateBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    full_name: str | None = Field(None, max_length=256)
    city: str | None = Field(None, max_length=128)
    state: str | None = Field(None, max_length=128)
    country: str | None = Field(None, max_length=128)
    zip_code: str | None = Field(None, max_length=32)


class UserPatchBody(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(None, max_length=256)
    city: str | None = Field(None, max_length=128)
    state: str | None = Field(None, max_length=128)
    country: str | None = Field(None, max_length=128)
    zip_code: str | None = Field(None, max_length=32)


class UserPasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=256)


@router.get("/users")
def admin_list_users(
    db: Session = Depends(get_db),
    _: None = AdminDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = None,
) -> dict[str, Any]:
    stmt = select(User)
    cq = select(func.count()).select_from(User)
    if q and q.strip():
        pat = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(pat), User.full_name.ilike(pat)))
        cq = cq.where(or_(User.email.ilike(pat), User.full_name.ilike(pat)))
    total = int(db.scalar(cq) or 0)
    rows = list(
        db.scalars(stmt.order_by(User.created_at.desc()).offset(offset).limit(limit)).all()
    )
    return {"data": {"users": [_user_out(u) for u in rows], "total_count": total}, "meta": _meta()}


@router.post("/users")
def admin_create_user(body: UserCreateBody, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    em = body.email.lower().strip()
    if db.scalar(select(User).where(User.email == em)):
        raise HTTPException(status_code=409, detail={"code": "EMAIL_IN_USE", "message": "email already exists"})
    u = User(
        email=em,
        password_hash=hash_password(body.password),
        full_name=(body.full_name or "").strip() or None,
        city=(body.city or "").strip() or None,
        state=(body.state or "").strip() or None,
        country=(body.country or "").strip() or None,
        zip_code=(body.zip_code or "").strip() or None,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    log.info("admin_user_created", user_id=str(u.id))
    return {"data": _user_out(u), "meta": {}}


@router.get("/users/{user_id}")
def admin_get_user(user_id: int, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "user not found"})
    mems = list(db.scalars(select(TenantMembership).where(TenantMembership.user_id == user_id)).all())
    tenants = []
    for m in mems:
        t = db.get(Tenant, m.tenant_id)
        tenants.append(
            {
                "membership_id": str(m.id),
                "tenant_id": m.tenant_id,
                "tenant_name": t.name if t else m.tenant_id,
                "role": m.role,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
        )
    return {"data": {**_user_out(u), "memberships": tenants}, "meta": {}}


@router.patch("/users/{user_id}")
def admin_patch_user(
    user_id: int, body: UserPatchBody, db: Session = Depends(get_db), _: None = AdminDep
) -> dict[str, Any]:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "user not found"})
    if body.email is not None:
        em = body.email.lower().strip()
        existing = db.scalar(select(User).where(User.email == em, User.id != user_id))
        if existing:
            raise HTTPException(status_code=409, detail={"code": "EMAIL_IN_USE", "message": "email already exists"})
        u.email = em
    if body.full_name is not None:
        u.full_name = body.full_name.strip() or None
    if body.city is not None:
        u.city = body.city.strip() or None
    if body.state is not None:
        u.state = body.state.strip() or None
    if body.country is not None:
        u.country = body.country.strip() or None
    if body.zip_code is not None:
        u.zip_code = body.zip_code.strip() or None
    db.commit()
    db.refresh(u)
    return {"data": _user_out(u), "meta": {}}


@router.post("/users/{user_id}/password")
def admin_set_user_password(
    user_id: int, body: UserPasswordBody, db: Session = Depends(get_db), _: None = AdminDep
) -> dict[str, Any]:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "user not found"})
    u.password_hash = hash_password(body.password)
    db.commit()
    log.info("admin_user_password_set", user_id=str(user_id))
    return {"data": {"ok": True}, "meta": {}}


@router.delete("/users/{user_id}")
def admin_delete_user(user_id: int, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "user not found"})
    db.delete(u)
    db.commit()
    log.warning("admin_user_deleted", user_id=str(user_id))
    return {"data": {"deleted": True}, "meta": {}}


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------


def _tenant_out(t: Tenant) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "slug": t.slug,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


class TenantCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    slug: str | None = Field(default=None, max_length=128)


class TenantPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    slug: str | None = Field(default=None, max_length=128)


@router.get("/tenants")
def admin_list_tenants(
    db: Session = Depends(get_db),
    _: None = AdminDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = None,
) -> dict[str, Any]:
    stmt = select(Tenant)
    if q and q.strip():
        stmt = stmt.where(or_(Tenant.name.ilike(f"%{q.strip()}%"), Tenant.id.ilike(f"%{q.strip()}%")))
    cq = select(func.count()).select_from(Tenant)
    if q and q.strip():
        cq = cq.where(or_(Tenant.name.ilike(f"%{q.strip()}%"), Tenant.id.ilike(f"%{q.strip()}%")))
    total = int(db.scalar(cq) or 0)
    rows = list(db.scalars(stmt.order_by(Tenant.created_at.desc()).offset(offset).limit(limit)).all())
    return {"data": {"tenants": [_tenant_out(t) for t in rows], "total_count": total}, "meta": {}}


@router.post("/tenants")
def admin_create_tenant(body: TenantCreateBody, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    tid = str(uuid.uuid4())
    slug = body.slug.strip() if body.slug else None
    if slug:
        if db.scalar(select(Tenant).where(Tenant.slug == slug)):
            raise HTTPException(status_code=409, detail={"code": "SLUG_IN_USE", "message": "slug already used"})
    t = Tenant(id=tid, name=body.name.strip(), slug=slug)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"data": _tenant_out(t), "meta": {}}


@router.get("/tenants/{tenant_id}")
def admin_get_tenant(tenant_id: str, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "tenant not found"})
    bill = db.get(TenantBilling, tenant_id)
    n_proj = int(db.scalar(select(func.count()).select_from(Project).where(Project.tenant_id == tenant_id)) or 0)
    return {
        "data": {
            **_tenant_out(t),
            "billing": _billing_out(bill, db) if bill else None,
            "project_count": n_proj,
        },
        "meta": {},
    }


@router.patch("/tenants/{tenant_id}")
def admin_patch_tenant(
    tenant_id: str, body: TenantPatchBody, db: Session = Depends(get_db), _: None = AdminDep
) -> dict[str, Any]:
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "tenant not found"})
    if body.name is not None:
        t.name = body.name.strip()
    if body.slug is not None:
        s = body.slug.strip() or None
        if s:
            ex = db.scalar(select(Tenant).where(Tenant.slug == s, Tenant.id != tenant_id))
            if ex:
                raise HTTPException(status_code=409, detail={"code": "SLUG_IN_USE", "message": "slug already used"})
        t.slug = s
    db.commit()
    db.refresh(t)
    invalidate_runtime_settings_cache(tenant_id)
    return {"data": _tenant_out(t), "meta": {}}


@router.delete("/tenants/{tenant_id}")
def admin_delete_tenant(tenant_id: str, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "tenant not found"})
    db.delete(t)
    db.commit()
    invalidate_runtime_settings_cache(tenant_id)
    log.warning("admin_tenant_deleted", tenant_id=tenant_id)
    return {"data": {"deleted": True}, "meta": {}}


# ---------------------------------------------------------------------------
# Memberships (permissions)
# ---------------------------------------------------------------------------


ALLOWED_ROLES = frozenset({"owner", "admin", "member"})


class MembershipCreateBody(BaseModel):
    """Provide one user selector (id, email, or full name) and one tenant selector (id or name)."""

    user_id: int | None = None
    user_email: str | None = Field(None, max_length=320)
    user_full_name: str | None = Field(None, max_length=256)
    tenant_id: str | None = Field(None, max_length=64)
    tenant_name: str | None = Field(None, max_length=256)
    role: str = "member"


class MembershipPatchBody(BaseModel):
    role: str


def _mem_out(db: Session, m: TenantMembership) -> dict[str, Any]:
    u = db.get(User, m.user_id)
    t = db.get(Tenant, m.tenant_id)
    fn = (u.full_name or "").strip() if u else ""
    return {
        "id": str(m.id),
        "user_id": str(m.user_id),
        "tenant_id": m.tenant_id,
        "user_email": u.email if u else None,
        "user_full_name": fn or None,
        "tenant_name": t.name if t else None,
        "role": m.role,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/memberships")
def admin_list_memberships(
    db: Session = Depends(get_db),
    _: None = AdminDep,
    tenant_id: str | None = None,
    user_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    base = select(TenantMembership)
    if tenant_id:
        base = base.where(TenantMembership.tenant_id == tenant_id.strip())
    if user_id:
        base = base.where(TenantMembership.user_id == user_id)
    cq = select(func.count()).select_from(TenantMembership)
    if tenant_id:
        cq = cq.where(TenantMembership.tenant_id == tenant_id.strip())
    if user_id:
        cq = cq.where(TenantMembership.user_id == user_id)
    total = int(db.scalar(cq) or 0)
    rows = list(db.scalars(base.order_by(TenantMembership.created_at.desc()).offset(offset).limit(limit)).all())
    return {"data": {"memberships": [_mem_out(db, m) for m in rows], "total_count": total}, "meta": {}}


def _resolve_membership_user(db: Session, body: MembershipCreateBody) -> User:
    if body.user_id is not None:
        u = db.get(User, body.user_id)
        if not u:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "user not found"})
        return u
    if body.user_email is not None and str(body.user_email).strip():
        em = str(body.user_email).lower().strip()
        u = db.scalar(select(User).where(User.email == em))
        if not u:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "user not found"})
        return u
    if body.user_full_name is not None and body.user_full_name.strip():
        fn = body.user_full_name.strip()
        n = func.lower(func.trim(User.full_name))
        matches = list(db.scalars(select(User).where(n == fn.lower())).all())
        if len(matches) == 1:
            return matches[0]
        if len(matches) == 0:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": "no user with that full name"},
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AMBIGUOUS_USER_NAME",
                "message": "multiple users share this full name; use email or user id",
            },
        )
    raise HTTPException(
        status_code=422,
        detail={"code": "BAD_REQUEST", "message": "provide user_id, user_email, or user_full_name"},
    )


def _resolve_membership_tenant(db: Session, body: MembershipCreateBody) -> Tenant:
    if body.tenant_id is not None and body.tenant_id.strip():
        tid = body.tenant_id.strip()
        t = db.get(Tenant, tid)
        if not t:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "tenant not found"})
        return t
    if body.tenant_name is not None and body.tenant_name.strip():
        tn = body.tenant_name.strip()
        n = func.lower(func.trim(Tenant.name))
        matches = list(db.scalars(select(Tenant).where(n == tn.lower())).all())
        if len(matches) == 1:
            return matches[0]
        if len(matches) == 0:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": "no workspace with that name"},
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AMBIGUOUS_TENANT_NAME",
                "message": "multiple workspaces share this name; use tenant id",
            },
        )
    raise HTTPException(
        status_code=422,
        detail={"code": "BAD_REQUEST", "message": "provide tenant_id or tenant_name"},
    )


@router.post("/memberships")
def admin_create_membership(body: MembershipCreateBody, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail={"code": "BAD_ROLE", "message": f"role must be one of {sorted(ALLOWED_ROLES)}"})
    u = _resolve_membership_user(db, body)
    t = _resolve_membership_tenant(db, body)
    ex = db.scalar(
        select(TenantMembership).where(TenantMembership.user_id == u.id, TenantMembership.tenant_id == t.id)
    )
    if ex:
        raise HTTPException(status_code=409, detail={"code": "ALREADY_MEMBER", "message": "membership exists"})
    m = TenantMembership(id=uuid.uuid4(), user_id=u.id, tenant_id=t.id, role=body.role)
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"data": _mem_out(db, m), "meta": {}}


@router.patch("/memberships/{membership_id}")
def admin_patch_membership(
    membership_id: uuid.UUID, body: MembershipPatchBody, db: Session = Depends(get_db), _: None = AdminDep
) -> dict[str, Any]:
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail={"code": "BAD_ROLE", "message": f"role must be one of {sorted(ALLOWED_ROLES)}"})
    m = db.get(TenantMembership, membership_id)
    if not m:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "membership not found"})
    m.role = body.role
    db.commit()
    db.refresh(m)
    invalidate_runtime_settings_cache(m.tenant_id)
    return {"data": _mem_out(db, m), "meta": {}}


@router.delete("/memberships/{membership_id}")
def admin_delete_membership(membership_id: uuid.UUID, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    m = db.get(TenantMembership, membership_id)
    if not m:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "membership not found"})
    tid = m.tenant_id
    db.delete(m)
    db.commit()
    invalidate_runtime_settings_cache(tid)
    return {"data": {"deleted": True}, "meta": {}}


# ---------------------------------------------------------------------------
# Subscription plans
# ---------------------------------------------------------------------------


def _plan_out(p: SubscriptionPlan) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "slug": p.slug,
        "display_name": p.display_name,
        "description": p.description,
        "stripe_price_id": p.stripe_price_id,
        "stripe_product_id": p.stripe_product_id,
        "billing_interval": p.billing_interval,
        "is_active": p.is_active,
        "sort_order": p.sort_order,
        "entitlements_json": p.entitlements_json or {},
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


class PlanCreateBody(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    stripe_price_id: str | None = None
    stripe_product_id: str | None = None
    billing_interval: str = "month"
    is_active: bool = True
    sort_order: int = 0
    entitlements_json: dict[str, Any] = Field(default_factory=dict)


class PlanPatchBody(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    stripe_price_id: str | None = None
    stripe_product_id: str | None = None
    billing_interval: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    entitlements_json: dict[str, Any] | None = None


@router.get("/subscription-plans")
def admin_list_plans(db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    try:
        rows = list(db.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order.asc())).all())
        return {"data": {"plans": [_plan_out(p) for p in rows]}, "meta": {}}
    except SQLAlchemyError as e:
        log.warning("admin_subscription_plans_list_failed", error=str(e))
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return {
            "data": {"plans": []},
            "meta": _meta(
                warning="subscription_plans_unavailable",
                hint="Ensure migrations are applied: `alembic upgrade head` in apps/api.",
            ),
        }


@router.post("/subscription-plans")
def admin_create_plan(body: PlanCreateBody, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    if db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.slug == body.slug.strip())):
        raise HTTPException(status_code=409, detail={"code": "SLUG_IN_USE", "message": "plan slug exists"})
    p = SubscriptionPlan(
        id=uuid.uuid4(),
        slug=body.slug.strip(),
        display_name=body.display_name.strip(),
        description=body.description,
        stripe_price_id=body.stripe_price_id,
        stripe_product_id=body.stripe_product_id,
        billing_interval=body.billing_interval,
        is_active=body.is_active,
        sort_order=body.sort_order,
        entitlements_json=dict(body.entitlements_json or {}),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"data": _plan_out(p), "meta": {}}


@router.get("/subscription-plans/{plan_id}")
def admin_get_plan(plan_id: uuid.UUID, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    p = db.get(SubscriptionPlan, plan_id)
    if not p:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "plan not found"})
    return {"data": _plan_out(p), "meta": {}}


@router.patch("/subscription-plans/{plan_id}")
def admin_patch_plan(plan_id: uuid.UUID, body: PlanPatchBody, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    p = db.get(SubscriptionPlan, plan_id)
    if not p:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "plan not found"})
    if body.display_name is not None:
        p.display_name = body.display_name.strip()
    if body.description is not None:
        p.description = body.description
    if body.stripe_price_id is not None:
        p.stripe_price_id = body.stripe_price_id or None
    if body.stripe_product_id is not None:
        p.stripe_product_id = body.stripe_product_id or None
    if body.billing_interval is not None:
        p.billing_interval = body.billing_interval
    if body.is_active is not None:
        p.is_active = body.is_active
    if body.sort_order is not None:
        p.sort_order = body.sort_order
    if body.entitlements_json is not None:
        p.entitlements_json = dict(body.entitlements_json)
    db.commit()
    db.refresh(p)
    return {"data": _plan_out(p), "meta": {}}


@router.delete("/subscription-plans/{plan_id}")
def admin_delete_plan(plan_id: uuid.UUID, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    p = db.get(SubscriptionPlan, plan_id)
    if not p:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "plan not found"})
    db.delete(p)
    db.commit()
    return {"data": {"deleted": True}, "meta": {}}


# ---------------------------------------------------------------------------
# Tenant billing (subscriptions)
# ---------------------------------------------------------------------------


def _billing_out(b: TenantBilling, db: Session) -> dict[str, Any]:
    plan_name = None
    if b.plan_id:
        pl = db.get(SubscriptionPlan, b.plan_id)
        plan_name = pl.display_name if pl else None
    return {
        "tenant_id": b.tenant_id,
        "stripe_customer_id": b.stripe_customer_id,
        "stripe_subscription_id": b.stripe_subscription_id,
        "plan_id": str(b.plan_id) if b.plan_id else None,
        "plan_display_name": plan_name,
        "status": b.status,
        "current_period_end": b.current_period_end.isoformat() if b.current_period_end else None,
        "entitlements_override_json": b.entitlements_override_json,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }


def _billing_placeholder(tenant_id: str) -> dict[str, Any]:
    """Shape matches `_billing_out` when no `TenantBilling` row exists yet (admin can PATCH to create)."""
    return {
        "tenant_id": tenant_id,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "plan_id": None,
        "plan_display_name": None,
        "status": "none",
        "current_period_end": None,
        "entitlements_override_json": None,
        "updated_at": None,
    }


class TenantBillingPatchBody(BaseModel):
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    plan_id: uuid.UUID | None = None
    status: str | None = None
    current_period_end: datetime | None = None
    entitlements_override_json: dict[str, Any] | None = None


@router.get("/tenant-billing")
def admin_list_tenant_billing(
    db: Session = Depends(get_db),
    _: None = AdminDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        total = int(db.scalar(select(func.count()).select_from(TenantBilling)) or 0)
        rows = list(
            db.scalars(select(TenantBilling).order_by(TenantBilling.updated_at.desc()).offset(offset).limit(limit)).all()
        )
        return {
            "data": {"items": [_billing_out(b, db) for b in rows], "total_count": total},
            "meta": {},
        }
    except SQLAlchemyError as e:
        log.warning("admin_tenant_billing_list_failed", error=str(e))
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return {
            "data": {"items": [], "total_count": 0},
            "meta": _meta(
                warning="tenant_billing_unavailable",
                hint="Ensure migrations are applied: `alembic upgrade head` in apps/api.",
            ),
        }


@router.get("/tenant-billing/{tenant_id}")
def admin_get_tenant_billing(tenant_id: str, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    tid = (tenant_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail={"code": "BAD_REQUEST", "message": "tenant_id required"})
    t = db.get(Tenant, tid)
    if not t:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "tenant not found"})
    b = db.get(TenantBilling, tid)
    if not b:
        return {"data": _billing_placeholder(tid), "meta": {"billing_row_present": False}}
    return {"data": _billing_out(b, db), "meta": {"billing_row_present": True}}


@router.patch("/tenant-billing/{tenant_id}")
def admin_patch_tenant_billing(
    tenant_id: str, body: TenantBillingPatchBody, db: Session = Depends(get_db), _: None = AdminDep
) -> dict[str, Any]:
    tid = (tenant_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail={"code": "BAD_REQUEST", "message": "tenant_id required"})
    b = db.get(TenantBilling, tid)
    if not b:
        if not db.get(Tenant, tid):
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "tenant not found"})
        b = TenantBilling(tenant_id=tid, status="none")
        db.add(b)
    if body.stripe_customer_id is not None:
        b.stripe_customer_id = body.stripe_customer_id or None
    if body.stripe_subscription_id is not None:
        b.stripe_subscription_id = body.stripe_subscription_id or None
    if body.plan_id is not None:
        if body.plan_id and not db.get(SubscriptionPlan, body.plan_id):
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "plan not found"})
        b.plan_id = body.plan_id
    if body.status is not None:
        b.status = body.status.strip() or "none"
    if body.current_period_end is not None:
        b.current_period_end = body.current_period_end
    if body.entitlements_override_json is not None:
        b.entitlements_override_json = body.entitlements_override_json
    db.commit()
    db.refresh(b)
    invalidate_runtime_settings_cache(tid)
    return {"data": _billing_out(b, db), "meta": {}}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def _tenant_names_by_id(db: Session, tenant_ids: Iterable[str]) -> dict[str, str]:
    ids = {str(x) for x in tenant_ids if x}
    if not ids:
        return {}
    rows = list(db.scalars(select(Tenant).where(Tenant.id.in_(ids))).all())
    return {t.id: t.name for t in rows}


def _project_admin_out(p: Project, *, tenant_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "tenant_id": p.tenant_id,
        "tenant_name": tenant_name,
        "title": p.title,
        "topic": p.topic[:200] + ("…" if len(p.topic) > 200 else ""),
        "status": p.status,
        "workflow_phase": p.workflow_phase,
        "target_runtime_minutes": p.target_runtime_minutes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


class ProjectPatchAdminBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: str | None = Field(default=None, max_length=32)


@router.get("/projects")
def admin_list_projects(
    db: Session = Depends(get_db),
    _: None = AdminDep,
    tenant_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = None,
) -> dict[str, Any]:
    stmt = select(Project)
    if tenant_id:
        stmt = stmt.where(Project.tenant_id == tenant_id.strip())
    if q and q.strip():
        stmt = stmt.where(or_(Project.title.ilike(f"%{q.strip()}%"), Project.topic.ilike(f"%{q.strip()}%")))
    cq = select(func.count()).select_from(Project)
    if tenant_id:
        cq = cq.where(Project.tenant_id == tenant_id.strip())
    if q and q.strip():
        cq = cq.where(or_(Project.title.ilike(f"%{q.strip()}%"), Project.topic.ilike(f"%{q.strip()}%")))
    total = int(db.scalar(cq) or 0)
    rows = list(db.scalars(stmt.order_by(Project.updated_at.desc()).offset(offset).limit(limit)).all())
    name_by_tid = _tenant_names_by_id(db, (p.tenant_id for p in rows if p.tenant_id))
    projects_out = [
        _project_admin_out(p, tenant_name=name_by_tid.get(str(p.tenant_id)) if p.tenant_id else None)
        for p in rows
    ]
    return {"data": {"projects": projects_out, "total_count": total}, "meta": {}}


@router.get("/projects/{project_id}")
def admin_get_project(project_id: uuid.UUID, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    name_by_tid = _tenant_names_by_id(db, [p.tenant_id] if p.tenant_id else [])
    tn = name_by_tid.get(str(p.tenant_id)) if p.tenant_id else None
    return {
        "data": {
            **_project_admin_out(p, tenant_name=tn),
            "topic_full": p.topic,
        },
        "meta": {},
    }


@router.patch("/projects/{project_id}")
def admin_patch_project(
    project_id: uuid.UUID, body: ProjectPatchAdminBody, db: Session = Depends(get_db), _: None = AdminDep
) -> dict[str, Any]:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    if body.title is not None:
        p.title = body.title
    if body.status is not None:
        p.status = body.status
    db.commit()
    db.refresh(p)
    invalidate_runtime_settings_cache(p.tenant_id)
    name_by_tid = _tenant_names_by_id(db, [p.tenant_id] if p.tenant_id else [])
    tn = name_by_tid.get(str(p.tenant_id)) if p.tenant_id else None
    return {"data": _project_admin_out(p, tenant_name=tn), "meta": {}}


@router.delete("/projects/{project_id}")
def admin_delete_project(project_id: uuid.UUID, db: Session = Depends(get_db), _: None = AdminDep) -> dict[str, Any]:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    tid = p.tenant_id
    db.delete(p)
    db.commit()
    invalidate_runtime_settings_cache(tid)
    remove_generated_project_files(get_settings().local_storage_root, project_id)
    log.warning("admin_project_deleted", project_id=str(project_id))
    return {"data": {"deleted": True}, "meta": {}}


# ---------------------------------------------------------------------------
# Payments (audit log)
# ---------------------------------------------------------------------------


def _pay_out(e: BillingPaymentEvent, *, tenant_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "stripe_event_id": e.stripe_event_id,
        "event_type": e.event_type,
        "tenant_id": e.tenant_id,
        "tenant_name": tenant_name,
        "stripe_object_id": e.stripe_object_id,
        "amount_cents": e.amount_cents,
        "currency": e.currency,
        "livemode": e.livemode,
        "payload_summary_json": e.payload_summary_json,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/payments")
def admin_list_payments(
    db: Session = Depends(get_db),
    _: None = AdminDep,
    tenant_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        stmt = select(BillingPaymentEvent)
        if tenant_id:
            stmt = stmt.where(BillingPaymentEvent.tenant_id == tenant_id.strip())
        cq = select(func.count()).select_from(BillingPaymentEvent)
        if tenant_id:
            cq = cq.where(BillingPaymentEvent.tenant_id == tenant_id.strip())
        total = int(db.scalar(cq) or 0)
        rows = list(
            db.scalars(stmt.order_by(BillingPaymentEvent.created_at.desc()).offset(offset).limit(limit)).all()
        )
        tids = {str(e.tenant_id) for e in rows if e.tenant_id}
        name_by_tid: dict[str, str] = {}
        if tids:
            tenants = list(db.scalars(select(Tenant).where(Tenant.id.in_(tids))).all())
            name_by_tid = {t.id: t.name for t in tenants}
        events_out = [
            _pay_out(e, tenant_name=name_by_tid.get(str(e.tenant_id)) if e.tenant_id else None)
            for e in rows
        ]
        return {"data": {"events": events_out, "total_count": total}, "meta": {}}
    except SQLAlchemyError as e:
        log.warning("admin_payments_list_failed", error=str(e))
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return {
            "data": {"events": [], "total_count": 0},
            "meta": _meta(
                warning="billing_payment_events_unavailable",
                hint="Run `alembic upgrade head` in apps/api (migration 021 creates this table).",
            ),
        }


# ---------------------------------------------------------------------------
# Agent runs (management)
# ---------------------------------------------------------------------------


class AdminBudgetPipelineTestBody(BaseModel):
    """Same autonomous brief shape as ``scripts/budget_pipeline_test.py`` (placeholder images, local FFmpeg video; narration uses workspace TTS)."""

    title: str = Field(default="Budget pipeline test", min_length=1, max_length=500)
    topic: str = Field(default="", max_length=8000)
    target_runtime_minutes: int = Field(default=5, ge=2, le=120)
    mode: Literal["auto", "hands-off"] = "hands-off"
    tenant_id: str | None = Field(
        default=None,
        description="Workspace id; defaults to platform default tenant from settings.",
    )
    continue_pipeline: bool = Field(
        default=False,
        description="If true, enqueue on ``project_id`` with continue_from_existing (skip completed phases).",
    )
    project_id: uuid.UUID | None = Field(
        default=None,
        description="Existing project to resume; required when continue_pipeline is true.",
    )

    @model_validator(mode="after")
    def _budget_continue_rules(self) -> "AdminBudgetPipelineTestBody":
        if self.continue_pipeline:
            if self.project_id is None:
                raise ValueError("project_id is required when continue_pipeline is true")
        else:
            if not (self.topic or "").strip():
                raise ValueError("topic is required for a new budget pipeline run")
        return self


def _budget_smoke_pipeline_options(mode: str) -> dict[str, Any]:
    """Shared pipeline_options for admin budget / CLI smoke runs."""
    opts: dict[str, Any] = {
        "through": "full_video",
        "narration_granularity": "scene",
        "auto_generate_scene_videos": False,
    }
    if mode == "hands-off":
        opts["unattended"] = True
    return opts


@router.post("/budget-pipeline-test")
def admin_budget_pipeline_test(
    body: AdminBudgetPipelineTestBody,
    db: Session = Depends(get_db),
    _: None = AdminDep,
) -> JSONResponse:
    """Enqueue a budget/smoke pipeline run (parity with the CLI script; requires Celery worker)."""
    from director_api.api.routers.agent_runs import _project_from_brief
    from director_api.api.schemas.agent_run import AgentRunCreate, AgentRunOut
    from director_api.api.schemas.project import ProjectCreate, ProjectOut
    from director_api.tasks.worker_tasks import run_agent_run

    base = get_settings()
    tid = (body.tenant_id or "").strip() or (base.default_tenant_id or "").strip()
    if not tid:
        raise HTTPException(
            status_code=400,
            detail={"code": "TENANT_REQUIRED", "message": "tenant_id missing and no DEFAULT_TENANT_ID"},
        )
    if db.get(Tenant, tid) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"workspace not found: {tid}"},
        )

    settings = resolve_runtime_settings(db, base, tid)

    po: dict[str, Any] = _budget_smoke_pipeline_options(body.mode)

    if body.continue_pipeline:
        assert body.project_id is not None
        p = db.get(Project, body.project_id)
        if p is None or p.tenant_id != tid:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": "project not found for this workspace"},
            )
        po["continue_from_existing"] = True
    else:
        brief = ProjectCreate(
            title=body.title.strip(),
            topic=(body.topic or "").strip(),
            target_runtime_minutes=body.target_runtime_minutes,
            audience="general",
            tone="documentary",
            narration_style="preset:narrative_documentary",
            visual_style="preset:cinematic_documentary",
            preferred_image_provider="placeholder",
            preferred_video_provider="local_ffmpeg",
            # Omit speech provider so narration uses workspace ``active_speech_provider`` (real TTS), not FFmpeg ding.
        )
        create_body = AgentRunCreate(brief=brief, pipeline_options=po)
        # Platform admin smoke test: do not enforce workspace subscription gates (project cap,
        # hands-off / full-through entitlements). Caller is already authenticated via AdminDep.
        p = _project_from_brief(db, settings, create_body, tenant_id_override=tid)
        po["continue_from_existing"] = False

    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=p.id,
        started_by_user_id=None,
        status="queued",
        steps_json=[],
        pipeline_options_json=po,
        pipeline_control_json={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_agent_run.delay(str(run.id))
    log.info(
        "admin_budget_pipeline_enqueued",
        agent_run_id=str(run.id),
        project_id=str(p.id),
        tenant_id=tid,
        continue_pipeline=bool(body.continue_pipeline),
    )
    hint = (
        "Continuing on existing project — worker skips phases already satisfied (oversight + resume rules). "
        "Poll GET /v1/agent-runs/{id} until terminal status."
        if body.continue_pipeline
        else "Same as scripts/budget_pipeline_test.py — poll GET /v1/agent-runs/{id} until terminal status."
    )
    payload = {
        "data": {
            "agent_run": AgentRunOut.model_validate(run).model_dump(mode="json"),
            "project": ProjectOut.model_validate(p).model_dump(mode="json"),
            "poll_url": f"/v1/agent-runs/{run.id}",
            "hint": hint,
        },
        "meta": {},
    }
    return JSONResponse(status_code=202, content=payload)


def _run_out(r: AgentRun, *, tenant_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "tenant_id": r.tenant_id,
        "tenant_name": tenant_name,
        "project_id": str(r.project_id),
        "status": r.status,
        "current_step": r.current_step,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


@router.get("/agent-runs")
def admin_list_agent_runs(
    db: Session = Depends(get_db),
    _: None = AdminDep,
    tenant_id: str | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = Query(40, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    stmt = select(AgentRun)
    if tenant_id:
        stmt = stmt.where(AgentRun.tenant_id == tenant_id.strip())
    if project_id:
        stmt = stmt.where(AgentRun.project_id == project_id)
    cq = select(func.count()).select_from(AgentRun)
    if tenant_id:
        cq = cq.where(AgentRun.tenant_id == tenant_id.strip())
    if project_id:
        cq = cq.where(AgentRun.project_id == project_id)
    total = int(db.scalar(cq) or 0)
    rows = list(db.scalars(stmt.order_by(AgentRun.created_at.desc()).offset(offset).limit(limit)).all())
    name_by_tid = _tenant_names_by_id(db, (r.tenant_id for r in rows if r.tenant_id))
    runs_out = [
        _run_out(r, tenant_name=name_by_tid.get(str(r.tenant_id)) if r.tenant_id else None) for r in rows
    ]
    return {"data": {"agent_runs": runs_out, "total_count": total}, "meta": {}}


@router.get("/agent-runs/{agent_run_id}")
def admin_get_agent_run(
    agent_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: None = AdminDep,
) -> dict[str, Any]:
    """Fetch one agent run (any workspace) — for admin UI polling."""
    r = db.get(AgentRun, agent_run_id)
    if not r:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    return {"data": AgentRunOut.model_validate(r).model_dump(mode="json"), "meta": {}}


@router.post("/agent-runs/{agent_run_id}/control")
def admin_agent_run_control(
    agent_run_id: uuid.UUID,
    body: AgentRunPipelineControl,
    db: Session = Depends(get_db),
    _: None = AdminDep,
) -> dict[str, Any]:
    """Pause, resume, or stop any agent run (platform admin). Same semantics as ``POST /v1/agent-runs/{id}/control``."""
    r = db.get(AgentRun, agent_run_id)
    if not r:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    if body.action == "stop" and r.status in _TERMINAL_STATUSES:
        return {"data": AgentRunOut.model_validate(r).model_dump(mode="json"), "meta": {}}
    out = _handle_agent_run_control(db, r, body)
    return {"data": AgentRunOut.model_validate(out).model_dump(mode="json"), "meta": {}}


@router.delete("/agent-runs/{agent_run_id}")
def admin_delete_agent_run(
    agent_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: None = AdminDep,
) -> Response:
    """Delete a terminal agent run row (any workspace)."""
    r = db.get(AgentRun, agent_run_id)
    if not r:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "agent run not found"})
    if r.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AGENT_RUN_ACTIVE",
                "message": "cannot delete an active run — stop it first, then delete",
            },
        )
    db.delete(r)
    db.commit()
    log.info("admin_agent_run_deleted", agent_run_id=str(agent_run_id))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Jobs (management)
# ---------------------------------------------------------------------------


def _job_out(j: Job, *, tenant_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "tenant_id": j.tenant_id,
        "tenant_name": tenant_name,
        "project_id": str(j.project_id) if j.project_id else None,
        "type": j.type,
        "status": j.status,
        "created_at": j.created_at.isoformat() if hasattr(j, "created_at") and j.created_at else None,
    }


@router.get("/jobs")
def admin_list_jobs(
    db: Session = Depends(get_db),
    _: None = AdminDep,
    tenant_id: str | None = None,
    status: str | None = None,
    limit: int = Query(40, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    stmt = select(Job)
    if tenant_id:
        stmt = stmt.where(Job.tenant_id == tenant_id.strip())
    if status:
        stmt = stmt.where(Job.status == status.strip())
    cq = select(func.count()).select_from(Job)
    if tenant_id:
        cq = cq.where(Job.tenant_id == tenant_id.strip())
    if status:
        cq = cq.where(Job.status == status.strip())
    total = int(db.scalar(cq) or 0)
    rows = list(db.scalars(stmt.order_by(Job.created_at.desc()).offset(offset).limit(limit)).all())
    name_by_tid = _tenant_names_by_id(db, (j.tenant_id for j in rows if j.tenant_id))
    jobs_out = [
        _job_out(j, tenant_name=name_by_tid.get(str(j.tenant_id)) if j.tenant_id else None) for j in rows
    ]
    return {"data": {"jobs": jobs_out, "total_count": total}, "meta": {}}

