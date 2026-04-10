"""LLM token usage persistence and rough USD estimates for dashboards."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from director_api.db.models import UsageRecord
from director_api.services.usage_credits import CREDITS_PER_USD, credits_from_llm_cost_usd

log = structlog.get_logger(__name__)

# USD per 1M tokens (input / output) — approximate; override via docs when pricing changes.
_MODEL_PRICE_PER_M: list[tuple[re.Pattern[str], tuple[float, float]]] = [
    (re.compile(r"gpt-4o-mini", re.I), (0.15, 0.60)),
    (re.compile(r"gpt-4\.1-mini", re.I), (0.40, 1.60)),
    (re.compile(r"gpt-4\.1", re.I), (2.00, 8.00)),
    (re.compile(r"gpt-4o(?!-mini)", re.I), (2.50, 10.00)),
    (re.compile(r"gpt-4-turbo", re.I), (10.00, 30.00)),
    (re.compile(r"gpt-3\.5-turbo", re.I), (0.50, 1.50)),
    (re.compile(r"claude-3\.5-sonnet", re.I), (3.00, 15.00)),
    (re.compile(r"claude-3", re.I), (0.25, 1.25)),
    (re.compile(r"grok", re.I), (2.00, 10.00)),
    (re.compile(r"gemini", re.I), (0.35, 1.05)),
]
_DEFAULT_PRICE_PER_M = (1.00, 3.00)


def _price_per_million_for_model(model: str) -> tuple[float, float]:
    m = (model or "").strip()
    if not m:
        return _DEFAULT_PRICE_PER_M
    for pat, prices in _MODEL_PRICE_PER_M:
        if pat.search(m):
            return prices
    return _DEFAULT_PRICE_PER_M


def estimate_llm_cost_usd(*, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp_m, out_m = _price_per_million_for_model(model)
    pt = max(0, int(prompt_tokens))
    ct = max(0, int(completion_tokens))
    return (pt / 1_000_000.0) * inp_m + (ct / 1_000_000.0) * out_m


def parse_openai_chat_usage(resp: Any) -> dict[str, Any] | None:
    """Extract token counts from OpenAI SDK chat completion response."""
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return None
        pt = int(getattr(u, "prompt_tokens", None) or getattr(u, "input_tokens", None) or 0)
        ct = int(getattr(u, "completion_tokens", None) or getattr(u, "output_tokens", None) or 0)
        tt = int(getattr(u, "total_tokens", None) or (pt + ct))
        if pt == 0 and ct == 0 and tt == 0:
            return None
        return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
    except Exception:
        log.warning("openai_usage_parse_failed")
        return None


def parse_openrouter_usage_json(data: dict[str, Any]) -> dict[str, Any] | None:
    u = data.get("usage")
    if not isinstance(u, dict):
        return None
    try:
        pt = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        ct = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
        tt = int(u.get("total_tokens") or (pt + ct))
        if pt == 0 and ct == 0 and tt == 0:
            return None
        return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
    except (TypeError, ValueError):
        return None


def append_llm_usage_sink(
    sink: list[dict[str, Any]] | None,
    *,
    provider: str,
    model: str,
    service_type: str,
    usage: dict[str, Any] | None,
) -> None:
    if not sink or not usage:
        return
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    if pt == 0 and ct == 0:
        return
    sink.append(
        {
            "provider": provider,
            "model": model,
            "service_type": service_type,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": int(usage.get("total_tokens") or (pt + ct)),
        }
    )


def parse_agents_usage(usage_obj: Any) -> dict[str, Any] | None:
    """Usage object from OpenAI Agents SDK (agents.usage.Usage)."""
    try:
        pt = int(getattr(usage_obj, "input_tokens", None) or 0)
        ct = int(getattr(usage_obj, "output_tokens", None) or 0)
        tt = int(getattr(usage_obj, "total_tokens", None) or (pt + ct))
        if pt == 0 and ct == 0 and tt == 0:
            return None
        return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
    except Exception:
        return None


def persist_llm_usage_entries(
    db: Session,
    *,
    tenant_id: str,
    project_id: uuid.UUID | None,
    scene_id: uuid.UUID | None,
    asset_id: uuid.UUID | None,
    entries: list[dict[str, Any]],
) -> None:
    """Append UsageRecord rows for LLM calls. Each entry: provider, model, service_type, prompt_tokens, completion_tokens."""
    for e in entries:
        if not isinstance(e, dict):
            continue
        provider = str(e.get("provider") or "unknown")[:64]
        model = str(e.get("model") or "unknown")[:256]
        service_type = str(e.get("service_type") or "llm")[:64]
        try:
            pt = int(e.get("prompt_tokens") or 0)
            ct = int(e.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            continue
        if pt < 0 or ct < 0:
            continue
        total = int(e.get("total_tokens") or (pt + ct))
        if total <= 0 and pt == 0 and ct == 0:
            continue
        cost = float(e.get("cost_estimate_usd") or estimate_llm_cost_usd(model=model, prompt_tokens=pt, completion_tokens=ct))
        cr = float(e.get("credits")) if e.get("credits") is not None else credits_from_llm_cost_usd(cost)
        meta = {
            "model": model,
            "provider": provider,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "service_type": service_type,
        }
        if e.get("extra") and isinstance(e["extra"], dict):
            meta["extra"] = e["extra"]
        db.add(
            UsageRecord(
                id=uuid.uuid4(),
                tenant_id=str(tenant_id)[:64],
                project_id=project_id,
                scene_id=scene_id,
                asset_id=asset_id,
                provider=provider,
                service_type=service_type,
                units=float(total),
                unit_type="tokens",
                cost_estimate=cost,
                credits=cr,
                meta_json=meta,
            )
        )


def usage_summary_for_tenant(
    db: Session,
    *,
    tenant_id: str,
    days: int = 30,
) -> dict[str, Any]:
    """Aggregate token usage and estimated cost by model for the usage UI."""
    days = max(1, min(int(days or 30), 366))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    credit_row = func.coalesce(
        UsageRecord.credits,
        case(
            (UsageRecord.unit_type == "tokens", func.coalesce(UsageRecord.cost_estimate, 0.0) * CREDITS_PER_USD),
            else_=0.0,
        ),
    )
    total_credits = db.scalar(
        select(func.sum(credit_row)).where(UsageRecord.tenant_id == tenant_id).where(UsageRecord.created_at >= since)
    )
    total_credits_f = float(total_credits or 0.0)

    # JSONB text extraction
    model_expr = func.coalesce(UsageRecord.meta_json["model"].astext, "unknown")
    provider_expr = func.coalesce(UsageRecord.meta_json["provider"].astext, "unknown")

    pt_expr = func.coalesce(
        func.cast(UsageRecord.meta_json["prompt_tokens"].astext, Integer),
        0,
    )
    ct_expr = func.coalesce(
        func.cast(UsageRecord.meta_json["completion_tokens"].astext, Integer),
        0,
    )
    sum_pt = func.sum(pt_expr)
    sum_ct = func.sum(ct_expr)

    stmt = (
        select(
            model_expr.label("model"),
            provider_expr.label("provider"),
            sum_pt.label("prompt_tokens"),
            sum_ct.label("completion_tokens"),
            func.sum(func.coalesce(UsageRecord.cost_estimate, 0.0)).label("cost_usd"),
            func.sum(credit_row).label("credits"),
            func.count().label("calls"),
        )
        .where(UsageRecord.tenant_id == tenant_id)
        .where(UsageRecord.created_at >= since)
        .where(UsageRecord.unit_type == "tokens")
        .group_by(model_expr, provider_expr)
        .order_by((sum_pt + sum_ct).desc())
    )

    rows = db.execute(stmt).all()
    models: list[dict[str, Any]] = []
    tot_pt = tot_ct = 0
    tot_cost = 0.0
    tot_llm_credits = 0.0
    tot_calls = 0
    for r in rows:
        m = str(r.model or "unknown")
        p = str(r.provider or "unknown")
        pt = int(r.prompt_tokens or 0)
        ct = int(r.completion_tokens or 0)
        c = float(r.cost_usd or 0.0)
        cr = float(r.credits or 0.0)
        n = int(r.calls or 0)
        tot_pt += pt
        tot_ct += ct
        tot_cost += c
        tot_llm_credits += cr
        tot_calls += n
        models.append(
            {
                "model": m,
                "provider": p,
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": pt + ct,
                "estimated_cost_usd": round(c, 6),
                "credits": round(cr, 4),
                "llm_calls": n,
            }
        )

    return {
        "period_days": days,
        "since": since.isoformat(),
        "models": models,
        "totals": {
            "prompt_tokens": tot_pt,
            "completion_tokens": tot_ct,
            "total_tokens": tot_pt + tot_ct,
            "estimated_cost_usd": round(tot_cost, 6),
            "llm_calls": tot_calls,
            "director_credits": round(total_credits_f, 4),
            "llm_credits": round(tot_llm_credits, 4),
        },
    }
