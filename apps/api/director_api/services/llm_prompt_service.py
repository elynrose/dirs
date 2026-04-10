"""DB-backed LLM prompt definitions and per-user overrides."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from director_api.db.models import LlmPromptDefinition, UserLlmPromptOverride
from director_api.llm_prompt_catalog import LLM_PROMPT_SPECS, PROMPT_DEFAULTS, all_prompt_keys


def ensure_prompt_definitions_seeded(db: Session) -> None:
    """Insert missing catalog rows and align stored defaults with code (idempotent)."""
    rows_by_key = {r.prompt_key: r for r in db.scalars(select(LlmPromptDefinition)).all()}
    for spec in LLM_PROMPT_SPECS:
        row = rows_by_key.get(spec.prompt_key)
        if row is None:
            db.add(
                LlmPromptDefinition(
                    prompt_key=spec.prompt_key,
                    title=spec.title,
                    description=spec.description,
                    default_content=spec.default_content,
                    sort_order=spec.sort_order,
                )
            )
            continue
        if (
            row.default_content != spec.default_content
            or row.title != spec.title
            or (row.description or "") != (spec.description or "")
            or row.sort_order != spec.sort_order
        ):
            row.title = spec.title
            row.description = spec.description
            row.default_content = spec.default_content
            row.sort_order = spec.sort_order
    db.flush()


def build_resolved_prompt_map(db: Session, tenant_id: str, user_id: str | None) -> dict[str, str]:
    """Full key → effective text for this tenant and user (or workspace-anonymous when user_id is None)."""
    ensure_prompt_definitions_seeded(db)
    rows = db.scalars(select(LlmPromptDefinition)).all()
    by_key = {r.prompt_key: (r.default_content or "").strip() or PROMPT_DEFAULTS.get(r.prompt_key, "") for r in rows}
    out: dict[str, str] = {}
    for k in PROMPT_DEFAULTS:
        out[k] = (by_key.get(k) or PROMPT_DEFAULTS[k]).strip() or PROMPT_DEFAULTS[k]

    uid = int(user_id) if user_id else None
    q = select(UserLlmPromptOverride).where(UserLlmPromptOverride.tenant_id == tenant_id)
    if uid is not None:
        q = q.where(UserLlmPromptOverride.user_id == uid)
    else:
        q = q.where(UserLlmPromptOverride.user_id.is_(None))
    for ov in db.scalars(q).all():
        ck = ov.prompt_key
        if ck in out and isinstance(ov.content, str) and ov.content.strip():
            out[ck] = ov.content.strip()
    return out


def list_prompt_rows_for_api(db: Session, tenant_id: str, user_id: str | None) -> list[dict[str, Any]]:
    ensure_prompt_definitions_seeded(db)
    resolved = build_resolved_prompt_map(db, tenant_id, user_id)
    defs = db.scalars(select(LlmPromptDefinition).order_by(LlmPromptDefinition.sort_order.asc())).all()
    uid = int(user_id) if user_id else None
    q = select(UserLlmPromptOverride).where(UserLlmPromptOverride.tenant_id == tenant_id)
    if uid is not None:
        q = q.where(UserLlmPromptOverride.user_id == uid)
    else:
        q = q.where(UserLlmPromptOverride.user_id.is_(None))
    override_keys = {r.prompt_key for r in db.scalars(q).all()}
    out: list[dict[str, Any]] = []
    for d in defs:
        k = d.prompt_key
        out.append(
            {
                "prompt_key": k,
                "title": d.title,
                "description": d.description or "",
                "default_content": d.default_content,
                "effective_content": resolved.get(k, ""),
                "is_custom": k in override_keys,
            }
        )
    return out


def upsert_user_prompt_override(
    db: Session, tenant_id: str, user_id: str | None, prompt_key: str, content: str
) -> UserLlmPromptOverride:
    if prompt_key not in all_prompt_keys():
        raise ValueError("unknown_prompt_key")
    ensure_prompt_definitions_seeded(db)
    uid = int(user_id) if user_id else None
    q = select(UserLlmPromptOverride).where(
        UserLlmPromptOverride.tenant_id == tenant_id,
        UserLlmPromptOverride.prompt_key == prompt_key,
    )
    if uid is not None:
        q = q.where(UserLlmPromptOverride.user_id == uid)
    else:
        q = q.where(UserLlmPromptOverride.user_id.is_(None))
    row = db.scalars(q).first()
    if row is None:
        row = UserLlmPromptOverride(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=uid,
            prompt_key=prompt_key,
            content=content,
        )
        db.add(row)
    else:
        row.content = content
    db.flush()
    return row


def delete_user_prompt_override(db: Session, tenant_id: str, user_id: str | None, prompt_key: str) -> bool:
    uid = int(user_id) if user_id else None
    q = delete(UserLlmPromptOverride).where(
        UserLlmPromptOverride.tenant_id == tenant_id,
        UserLlmPromptOverride.prompt_key == prompt_key,
    )
    if uid is not None:
        q = q.where(UserLlmPromptOverride.user_id == uid)
    else:
        q = q.where(UserLlmPromptOverride.user_id.is_(None))
    res = db.execute(q)
    return (res.rowcount or 0) > 0
