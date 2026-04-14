"""Directely credits: normalize provider usage into a single internal currency.

LLM rows derive credits from ``cost_estimate`` (USD) × CREDITS_PER_USD so they stay aligned
with :func:`usage_accounting.estimate_llm_cost_usd`. Media/TTS use fixed tables (tunable).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import UsageRecord
from director_api.services.tenant_entitlements import (
    ENTITLEMENT_CREDITS_ENFORCE,
    ENTITLEMENT_MONTHLY_CREDITS,
    get_effective_entitlements,
)

log = structlog.get_logger(__name__)

# How many Directely credits correspond to one USD of estimated LLM cost.
CREDITS_PER_USD = 1000.0

# Rolling window for budget checks (matches default usage-summary period).
CREDIT_BUDGET_WINDOW_DAYS = 30


def credits_from_llm_cost_usd(cost_usd: float) -> float:
    c = max(0.0, float(cost_usd)) * CREDITS_PER_USD
    return round(c, 6)


def _float_meta(meta: dict[str, Any] | None, *keys: str) -> float | None:
    m = meta or {}
    for k in keys:
        v = m.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def compute_request_credits(
    *,
    provider: str,
    service_type: str,
    unit_type: str | None,
    units: float | None,
    meta: dict[str, Any] | None,
) -> float:
    """Credits for non-token usage (media jobs, generic requests, TTS)."""
    ut = (unit_type or "request").strip().lower()
    u = max(0.0, float(units or 0.0))
    prov = (provider or "").strip().lower()
    st = (service_type or "").strip().lower()
    m = meta or {}

    if ut == "tokens":
        return 0.0

    if ut == "tts_chars":
        # ~50 credits per 1k characters of narration.
        return round((u / 1000.0) * 50.0, 6)

    if st == "image_gen":
        if prov == "placeholder":
            return 0.5
        return 10.0

    if st == "video_gen":
        dur = _float_meta(m, "duration_sec", "duration") or 0.0
        if dur <= 0 and isinstance(m.get("clips"), (int, float)):
            try:
                dur = float(m["clips"]) * 10.0
            except (TypeError, ValueError):
                dur = 0.0
        base = 5.0
        per_sec = 0.25
        return round(base + min(120.0, max(0.0, dur)) * per_sec, 6)

    # Generic request-sized work
    return 1.0


def coalesce_record_credits(row: UsageRecord) -> float:
    """Best-effort credits for one row (stored value, or legacy fallback)."""
    if row.credits is not None:
        return float(row.credits)
    ut = (row.unit_type or "").strip().lower()
    if ut == "tokens" and row.cost_estimate is not None:
        return credits_from_llm_cost_usd(float(row.cost_estimate))
    return 0.0


def sum_credits_for_tenant(
    db: Session,
    *,
    tenant_id: str,
    since: datetime,
) -> float:
    rows = db.scalars(
        select(UsageRecord).where(UsageRecord.tenant_id == tenant_id).where(UsageRecord.created_at >= since)
    ).all()
    return float(sum(coalesce_record_credits(r) for r in rows))


def monthly_credits_cap(ent: dict[str, Any]) -> int | None:
    """None = unlimited."""
    raw = ent.get(ENTITLEMENT_MONTHLY_CREDITS)
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def assert_credit_budget(db: Session, settings: Settings, tenant_id: str) -> None:
    """Block expensive enqueue when workspace exceeded rolling credit budget.

    Controlled per workspace via plan + billing override entitlements: ``credits_enforce`` and
    ``monthly_credits`` (not environment variables).
    """
    if not settings.director_auth_enabled:
        return
    ent = get_effective_entitlements(db, tenant_id, auth_enabled=True)
    if not bool(ent.get(ENTITLEMENT_CREDITS_ENFORCE)):
        return
    cap = monthly_credits_cap(ent)
    if cap is None:
        return
    since = datetime.now(timezone.utc) - timedelta(days=CREDIT_BUDGET_WINDOW_DAYS)
    used = sum_credits_for_tenant(db, tenant_id=tenant_id, since=since)
    if used >= float(cap):
        log.warning("credit_budget_exceeded", tenant_id=tenant_id, used=used, cap=cap)
        raise HTTPException(
            status_code=402,
            detail={
                "code": "CREDITS_EXHAUSTED",
                "message": (
                    f"Workspace credit budget reached for the last {CREDIT_BUDGET_WINDOW_DAYS} days "
                    f"({used:.0f} / {cap}). Upgrade your plan or wait for the window to roll."
                ),
            },
        )
