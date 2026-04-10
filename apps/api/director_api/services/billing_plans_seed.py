"""Seed default subscription plans (idempotent; safe to call on each API boot)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.config import Settings, get_settings
from director_api.db.models import SubscriptionPlan
from director_api.services.platform_stripe_settings import effective_stripe_price_studio_monthly

log = structlog.get_logger(__name__)

# Monthly studio: broad product access; excludes chat, Telegram, full auto / hands-off, subtitles.
# Unlimited projects. Admin can edit rows in DB later; future admin UI will CRUD this table.
_STUDIO_MONTHLY_SLUG = "studio_monthly"
_STUDIO_MONTHLY_ENTITLEMENTS: dict[str, Any] = {
    "chat_enabled": False,
    "telegram_enabled": False,
    "max_projects": None,
    "full_through_automation_enabled": False,
    "hands_off_unattended_enabled": False,
    "subtitles_enabled": False,
    "monthly_credits": None,
    "credits_enforce": False,
}


def ensure_default_subscription_plans(db: Session, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    row = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.slug == _STUDIO_MONTHLY_SLUG))
    price_id = effective_stripe_price_studio_monthly(db, s)
    if row is None:
        db.add(
            SubscriptionPlan(
                id=uuid.uuid4(),
                slug=_STUDIO_MONTHLY_SLUG,
                display_name="Studio Monthly",
                description=(
                    "Monthly access to Director studio features. Excludes Chat, Telegram, full-through "
                    "automation, hands-off runs, and subtitle generation. Project count unlimited."
                ),
                stripe_price_id=price_id,
                stripe_product_id=None,
                billing_interval="month",
                is_active=True,
                sort_order=10,
                entitlements_json=dict(_STUDIO_MONTHLY_ENTITLEMENTS),
            )
        )
        log.info("subscription_plan_seeded", slug=_STUDIO_MONTHLY_SLUG)
        db.flush()
        return
    # Refresh Stripe price from env when operator sets it (optional convenience).
    if price_id and row.stripe_price_id != price_id:
        row.stripe_price_id = price_id
        db.flush()
