"""Singleton platform Stripe configuration: database overrides with env fallback."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import PlatformStripeSettings

_PLATFORM_ID = 1


def get_or_create_platform_stripe(db: Session) -> PlatformStripeSettings:
    row = db.get(PlatformStripeSettings, _PLATFORM_ID)
    if row is None:
        row = PlatformStripeSettings(id=_PLATFORM_ID)
        db.add(row)
        db.flush()
    return row


def _pick_str(db_val: str | None, env_val: str | None) -> str:
    if isinstance(db_val, str) and db_val.strip():
        return db_val.strip()
    if env_val is None:
        return ""
    return str(env_val).strip()


def resolve_effective_stripe_settings(db: Session, base: Settings) -> dict[str, Any]:
    """Merged Stripe fields for billing (DB wins when set)."""
    row = db.get(PlatformStripeSettings, _PLATFORM_ID)
    price_db = getattr(row, "stripe_price_studio_monthly", None) if row else None
    price_merged: str | None
    if isinstance(price_db, str) and price_db.strip():
        price_merged = price_db.strip()
    else:
        pe = getattr(base, "stripe_price_studio_monthly", None)
        price_merged = (str(pe).strip() or None) if pe is not None else None
    return {
        "stripe_secret_key": _pick_str(getattr(row, "stripe_secret_key", None), base.stripe_secret_key),
        "stripe_webhook_secret": _pick_str(getattr(row, "stripe_webhook_secret", None), base.stripe_webhook_secret),
        "stripe_publishable_key": _pick_str(getattr(row, "stripe_publishable_key", None), base.stripe_publishable_key),
        "billing_success_url": _pick_str(getattr(row, "billing_success_url", None), base.billing_success_url),
        "billing_cancel_url": _pick_str(getattr(row, "billing_cancel_url", None), base.billing_cancel_url),
        "stripe_price_studio_monthly": price_merged,
    }


def effective_stripe_price_studio_monthly(db: Session, base: Settings) -> str | None:
    """Default Stripe Price id for ``studio_monthly`` plan seeding."""
    return resolve_effective_stripe_settings(db, base).get("stripe_price_studio_monthly")
