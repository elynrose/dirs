"""Resolve per-tenant feature access from subscription plan + optional overrides.

When ``director_auth_enabled`` is false (legacy single-tenant), all gates are open.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import Project, SubscriptionPlan, TenantBilling

# Keys are stable for a future admin UI / API to edit plan JSON.
ENTITLEMENT_CHAT = "chat_enabled"
ENTITLEMENT_TELEGRAM = "telegram_enabled"
ENTITLEMENT_MAX_PROJECTS = "max_projects"  # int or null = unlimited
ENTITLEMENT_FULL_THROUGH = "full_through_automation_enabled"  # agent run through full_video
ENTITLEMENT_UNATTENDED = "hands_off_unattended_enabled"  # unattended: true
ENTITLEMENT_SUBTITLES = "subtitles_enabled"
# Rolling-window credit budget; null = unlimited. Requires ``credits_enforce`` true to block jobs.
ENTITLEMENT_MONTHLY_CREDITS = "monthly_credits"
ENTITLEMENT_CREDITS_ENFORCE = "credits_enforce"

_LEGACY_UNBOUND: dict[str, Any] = {
    ENTITLEMENT_CHAT: True,
    ENTITLEMENT_TELEGRAM: True,
    ENTITLEMENT_MAX_PROJECTS: None,
    ENTITLEMENT_FULL_THROUGH: True,
    ENTITLEMENT_UNATTENDED: True,
    ENTITLEMENT_SUBTITLES: True,
    ENTITLEMENT_MONTHLY_CREDITS: None,
    ENTITLEMENT_CREDITS_ENFORCE: False,
}

# Workspaces without an active paid subscription (auth mode only).
_FREE_DEFAULTS: dict[str, Any] = {
    ENTITLEMENT_CHAT: False,
    ENTITLEMENT_TELEGRAM: False,
    ENTITLEMENT_MAX_PROJECTS: 2,
    ENTITLEMENT_FULL_THROUGH: False,
    ENTITLEMENT_UNATTENDED: False,
    ENTITLEMENT_SUBTITLES: False,
    ENTITLEMENT_MONTHLY_CREDITS: None,
    ENTITLEMENT_CREDITS_ENFORCE: False,
}

_ACTIVE_SUB_STATUSES = frozenset({"active", "trialing"})


def entitlement_definitions_public() -> list[dict[str, Any]]:
    """Canonical keys for admin UI and JSON stored on plans / billing overrides.

    ``type`` is ``boolean`` | ``limit`` (integer or null for unlimited).
    """

    return [
        {
            "key": ENTITLEMENT_CHAT,
            "label": "Chat studio",
            "type": "boolean",
            "description": "Access to the Chat studio for this workspace.",
        },
        {
            "key": ENTITLEMENT_TELEGRAM,
            "label": "Telegram integration",
            "type": "boolean",
            "description": "Telegram bot / integration features.",
        },
        {
            "key": ENTITLEMENT_MAX_PROJECTS,
            "label": "Max projects",
            "type": "limit",
            "description": "Maximum projects for this workspace. Leave empty for unlimited.",
        },
        {
            "key": ENTITLEMENT_FULL_THROUGH,
            "label": "Full pipeline automation",
            "type": "boolean",
            "description": "Agent runs may continue through full video (full_through / full_video).",
        },
        {
            "key": ENTITLEMENT_UNATTENDED,
            "label": "Hands-off (unattended) runs",
            "type": "boolean",
            "description": "Unattended / hands-off agent run mode.",
        },
        {
            "key": ENTITLEMENT_SUBTITLES,
            "label": "Subtitle generation",
            "type": "boolean",
            "description": "Generate subtitles for narration.",
        },
        {
            "key": ENTITLEMENT_MONTHLY_CREDITS,
            "label": "Monthly credits (budget)",
            "type": "limit",
            "description": (
                "Director credits per rolling 30-day window. Empty = unlimited. "
                "Turn on “Enforce credit budget” to block jobs when usage reaches this cap."
            ),
        },
        {
            "key": ENTITLEMENT_CREDITS_ENFORCE,
            "label": "Enforce credit budget",
            "type": "boolean",
            "description": (
                "When enabled and a monthly credits cap is set, new jobs are blocked if the workspace "
                "has reached the rolling 30-day credit total."
            ),
        },
    ]


def _deep_merge_entitlements(base: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(base)
    if not extra:
        return out
    for k, v in extra.items():
        if v is not None:
            out[k] = v
    return out


def get_effective_entitlements(
    db: Session,
    tenant_id: str,
    *,
    auth_enabled: bool,
) -> dict[str, Any]:
    if not auth_enabled:
        return dict(_LEGACY_UNBOUND)

    tid = (tenant_id or "").strip()
    if not tid:
        return dict(_FREE_DEFAULTS)

    billing = db.get(TenantBilling, tid)
    base = dict(_FREE_DEFAULTS)

    if billing and billing.status in _ACTIVE_SUB_STATUSES and billing.plan_id:
        plan = db.get(SubscriptionPlan, billing.plan_id)
        if plan and isinstance(plan.entitlements_json, dict):
            base = _deep_merge_entitlements(base, plan.entitlements_json)

    if billing and isinstance(billing.entitlements_override_json, dict):
        base = _deep_merge_entitlements(base, billing.entitlements_override_json)

    return base


def count_tenant_projects(db: Session, tenant_id: str) -> int:
    tid = (tenant_id or "").strip()
    if not tid:
        return 0
    n = db.scalar(select(func.count()).select_from(Project).where(Project.tenant_id == tid))
    return int(n or 0)


def _max_projects(ent: dict[str, Any]) -> int | None:
    raw = ent.get(ENTITLEMENT_MAX_PROJECTS)
    if raw is None:
        return None
    try:
        n = int(raw)
        return max(0, n)
    except (TypeError, ValueError):
        return None


def assert_can_create_project(db: Session, tenant_id: str, *, auth_enabled: bool) -> None:
    from fastapi import HTTPException

    ent = get_effective_entitlements(db, tenant_id, auth_enabled=auth_enabled)
    cap = _max_projects(ent)
    if cap is None:
        return
    if count_tenant_projects(db, tenant_id) >= cap:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PROJECT_LIMIT",
                "message": f"Workspace project limit reached ({cap}). Upgrade your plan for more.",
            },
        )


def assert_agent_run_pipeline_allowed(
    pipeline_options: dict[str, Any],
    *,
    db: Session,
    tenant_id: str,
    auth_enabled: bool,
) -> None:
    from fastapi import HTTPException

    from director_api.services.agent_resume import parse_pipeline_options

    ent = get_effective_entitlements(db, tenant_id, auth_enabled=auth_enabled)
    _cont, through, unattended = parse_pipeline_options(pipeline_options)

    if unattended and not bool(ent.get(ENTITLEMENT_UNATTENDED, False)):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ENTITLEMENT_UNATTENDED",
                "message": "Hands-off (unattended) automation is not included in your plan.",
            },
        )
    if through == "full_video" and not bool(ent.get(ENTITLEMENT_FULL_THROUGH, False)):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ENTITLEMENT_FULL_PIPELINE",
                "message": "Full-through automation (through: full_video) is not included in your plan.",
            },
        )


def assert_subtitles_allowed(*, db: Session, tenant_id: str, auth_enabled: bool) -> None:
    from fastapi import HTTPException

    ent = get_effective_entitlements(db, tenant_id, auth_enabled=auth_enabled)
    if not bool(ent.get(ENTITLEMENT_SUBTITLES, False)):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ENTITLEMENT_SUBTITLES",
                "message": "Subtitle generation is not included in your plan.",
            },
        )


def assert_telegram_allowed(*, db: Session, tenant_id: str, auth_enabled: bool) -> None:
    from fastapi import HTTPException

    ent = get_effective_entitlements(db, tenant_id, auth_enabled=auth_enabled)
    if not bool(ent.get(ENTITLEMENT_TELEGRAM, False)):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ENTITLEMENT_TELEGRAM",
                "message": "Telegram integration is not included in your plan.",
            },
        )


def assert_chat_allowed(*, db: Session, tenant_id: str, auth_enabled: bool) -> None:
    from fastapi import HTTPException

    ent = get_effective_entitlements(db, tenant_id, auth_enabled=auth_enabled)
    if not bool(ent.get(ENTITLEMENT_CHAT, False)):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ENTITLEMENT_CHAT",
                "message": "Chat studio is not included in your plan.",
            },
        )


def billing_summary_for_tenant(db: Session, tenant_id: str) -> dict[str, Any]:
    tid = (tenant_id or "").strip()
    row = db.get(TenantBilling, tid) if tid else None
    plan_slug = None
    plan_name = None
    if row and row.plan_id:
        p = db.get(SubscriptionPlan, row.plan_id)
        if p:
            plan_slug = p.slug
            plan_name = p.display_name
    return {
        "status": row.status if row else "none",
        "plan_slug": plan_slug,
        "plan_display_name": plan_name,
        "current_period_end": row.current_period_end.isoformat() if row and row.current_period_end else None,
        "stripe_customer_id": row.stripe_customer_id if row else None,
    }
