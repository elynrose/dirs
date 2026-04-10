"""Stripe Checkout + webhooks; subscription plans listing (DB-driven for future admin)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.config import Settings, get_settings
from director_api.db.models import BillingPaymentEvent, SubscriptionPlan, Tenant, TenantBilling
from director_api.db.session import get_db
from director_api.services.billing_plans_seed import ensure_default_subscription_plans
from director_api.services.platform_stripe_settings import resolve_effective_stripe_settings
from director_api.services.tenant_entitlements import billing_summary_for_tenant

router = APIRouter(prefix="/billing", tags=["billing"])
log = structlog.get_logger(__name__)


@router.get("/plans")
def list_plans(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict[str, Any]:
    ensure_default_subscription_plans(db, settings)
    db.commit()
    rows = list(
        db.scalars(
            select(SubscriptionPlan)
            .where(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.display_name.asc())
        ).all()
    )
    eff = resolve_effective_stripe_settings(db, get_settings())
    pk = (eff.get("stripe_publishable_key") or "").strip() or None
    return {
        "data": {
            "plans": [
                {
                    "slug": p.slug,
                    "display_name": p.display_name,
                    "description": p.description,
                    "billing_interval": p.billing_interval,
                    "stripe_price_configured": bool((p.stripe_price_id or "").strip()),
                    "entitlements": p.entitlements_json or {},
                }
                for p in rows
            ],
            "stripe_publishable_key": pk,
        },
        "meta": meta,
    }


class CheckoutBody(BaseModel):
    plan_slug: str = Field(min_length=1, max_length=64)


@router.post("/checkout-session")
def create_checkout_session(
    body: CheckoutBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict[str, Any]:
    if not get_settings().director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    gs = get_settings()
    eff = resolve_effective_stripe_settings(db, gs)
    sk = (eff.get("stripe_secret_key") or "").strip()
    if not sk:
        raise HTTPException(
            status_code=503,
            detail={"code": "STRIPE_NOT_CONFIGURED", "message": "Stripe secret key is not configured on the server"},
        )
    ensure_default_subscription_plans(db, settings)
    db.commit()
    plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.slug == body.plan_slug.strip()))
    if not plan or not plan.is_active:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "unknown plan"})
    price = (plan.stripe_price_id or "").strip()
    if not price:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "STRIPE_PRICE_MISSING",
                "message": "Plan has no stripe_price_id; set it in Admin → Stripe or subscription_plans, or env STRIPE_PRICE_STUDIO_MONTHLY.",
            },
        )

    import stripe

    stripe.api_key = sk
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price, "quantity": 1}],
            success_url=eff.get("billing_success_url") or gs.billing_success_url,
            cancel_url=eff.get("billing_cancel_url") or gs.billing_cancel_url,
            client_reference_id=auth.tenant_id,
            subscription_data={
                "metadata": {
                    "tenant_id": auth.tenant_id,
                    "plan_slug": plan.slug,
                    "plan_id": str(plan.id),
                },
            },
            metadata={
                "tenant_id": auth.tenant_id,
                "plan_slug": plan.slug,
                "plan_id": str(plan.id),
            },
        )
    except Exception as exc:
        log.warning("stripe_checkout_failed", error=str(exc))
        raise HTTPException(
            status_code=502,
            detail={"code": "STRIPE_ERROR", "message": str(exc)},
        ) from exc

    url = session.get("url")
    if not url:
        raise HTTPException(status_code=502, detail={"code": "STRIPE_ERROR", "message": "no checkout url returned"})
    return {"data": {"url": url, "session_id": session.get("id")}, "meta": meta}


@router.get("/subscription")
def get_subscription(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict[str, Any]:
    if not get_settings().director_auth_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
    summary = billing_summary_for_tenant(db, auth.tenant_id)
    return {"data": summary, "meta": meta}


def _audit_stripe_event(db: Session, event: dict[str, Any]) -> None:
    """Best-effort idempotent audit row (duplicate stripe_event_id ignored)."""
    eid = str(event.get("id") or "").strip()
    et = str(event.get("type") or "").strip()
    if not eid or not et:
        return
    dup = db.scalar(
        select(func.count()).select_from(BillingPaymentEvent).where(BillingPaymentEvent.stripe_event_id == eid)
    )
    if int(dup or 0) > 0:
        return
    obj = event.get("data", {}).get("object") or {}
    meta = obj.get("metadata") or {}
    tid_raw = (meta.get("tenant_id") or "").strip() or None
    tid = tid_raw if tid_raw and db.get(Tenant, tid_raw) else None
    oid = str(obj.get("id") or "").strip() or None
    amt = obj.get("amount_paid")
    if not isinstance(amt, int):
        amt = obj.get("amount")
    if not isinstance(amt, int):
        amt = None
    cur = obj.get("currency")
    if isinstance(cur, str):
        cur = cur.lower()
    else:
        cur = None
    live = obj.get("livemode")
    if not isinstance(live, bool):
        live = None
    summary = {
        "object_type": obj.get("object"),
        "customer": obj.get("customer"),
        "subscription": obj.get("subscription"),
        "status": obj.get("status"),
    }
    db.add(
        BillingPaymentEvent(
            id=uuid.uuid4(),
            stripe_event_id=eid,
            event_type=et,
            tenant_id=tid,
            stripe_object_id=oid,
            amount_cents=amt,
            currency=cur,
            livemode=live,
            payload_summary_json=summary,
        )
    )
    db.flush()


def _sync_stripe_subscription(db: Session, sub: dict[str, Any]) -> None:
    meta = sub.get("metadata") or {}
    tid = (meta.get("tenant_id") or "").strip()
    if not tid:
        log.warning("stripe_subscription_no_tenant", subscription_id=sub.get("id"))
        return
    items = (sub.get("items") or {}).get("data") or []
    price_id = None
    if items:
        price_id = (items[0].get("price") or {}).get("id")
    plan = None
    if price_id:
        plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.stripe_price_id == price_id))
    status = str(sub.get("status") or "none")
    cpe = sub.get("current_period_end")
    period_end = None
    if isinstance(cpe, (int, float)):
        period_end = datetime.datetime.fromtimestamp(float(cpe), tz=datetime.timezone.utc)

    row = db.get(TenantBilling, tid)
    if row is None:
        row = TenantBilling(tenant_id=tid)
        db.add(row)
    row.stripe_customer_id = str(sub.get("customer") or row.stripe_customer_id or "") or row.stripe_customer_id
    row.stripe_subscription_id = str(sub.get("id") or "")
    row.plan_id = plan.id if plan else row.plan_id
    row.status = status
    row.current_period_end = period_end
    db.flush()
    log.info("tenant_billing_synced", tenant_id=tid, status=status, plan_id=str(row.plan_id) if row.plan_id else None)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    gs = get_settings()
    eff = resolve_effective_stripe_settings(db, gs)
    wh_secret = (eff.get("stripe_webhook_secret") or "").strip()
    sk = (eff.get("stripe_secret_key") or "").strip()
    if not wh_secret or not sk:
        raise HTTPException(status_code=503, detail={"code": "STRIPE_WEBHOOK_NOT_CONFIGURED", "message": "missing webhook secret"})

    import stripe

    payload = await request.body()
    sig = request.headers.get("stripe-signature") or ""
    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig, secret=wh_secret)
    except Exception as exc:
        log.warning("stripe_webhook_verify_failed", error=str(exc))
        raise HTTPException(status_code=400, detail={"code": "INVALID_SIGNATURE", "message": "invalid signature"}) from exc

    try:
        _audit_stripe_event(db, dict(event))
        db.flush()
    except Exception:
        log.exception("stripe_audit_record_failed")

    et = event.get("type")
    obj = event.get("data", {}).get("object") or {}

    try:
        if et == "checkout.session.completed" and obj.get("mode") == "subscription":
            sub_id = obj.get("subscription")
            if sub_id:
                stripe.api_key = sk
                sub = stripe.Subscription.retrieve(str(sub_id))
                _sync_stripe_subscription(db, dict(sub))
        elif et in ("customer.subscription.updated", "customer.subscription.deleted"):
            if et.endswith("deleted"):
                meta = obj.get("metadata") or {}
                tid = (meta.get("tenant_id") or "").strip()
                if not tid:
                    sid = str(obj.get("id") or "").strip()
                    if sid:
                        row = db.scalar(
                            select(TenantBilling).where(TenantBilling.stripe_subscription_id == sid)
                        )
                        if row:
                            tid = row.tenant_id
                if tid:
                    row = db.get(TenantBilling, tid)
                    if row:
                        row.status = "canceled"
                        row.stripe_subscription_id = None
                        row.plan_id = None
                        row.current_period_end = None
            else:
                _sync_stripe_subscription(db, obj)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("stripe_webhook_handler_failed", event_type=et)
        raise HTTPException(status_code=500, detail={"code": "WEBHOOK_HANDLER_ERROR", "message": "handler failed"})

    return {"status": "ok"}
