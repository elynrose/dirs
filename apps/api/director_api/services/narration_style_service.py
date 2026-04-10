from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import UserNarrationStyle
from director_api.style_presets import PRESET_PREFIX, USER_PREFIX, _NARRATION

log = structlog.get_logger(__name__)


def list_merged_styles(db: Session, tenant_id: str, user_id: int | None) -> list[dict]:
    """Built-in presets plus this user's custom rows."""
    out: list[dict] = []
    for pid, (label, prompt) in _NARRATION.items():
        out.append(
            {
                "ref": f"{PRESET_PREFIX}{pid}",
                "kind": "preset",
                "title": label,
                "prompt": prompt,
                "is_builtin": True,
            }
        )
    if user_id is None:
        return out
    rows = list(
        db.scalars(
            select(UserNarrationStyle)
            .where(UserNarrationStyle.tenant_id == tenant_id, UserNarrationStyle.user_id == user_id)
            .order_by(UserNarrationStyle.updated_at.desc())
        ).all()
    )
    for r in rows:
        out.append(
            {
                "ref": f"{USER_PREFIX}{r.id}",
                "kind": "custom",
                "title": r.title,
                "prompt": r.prompt_text or "",
                "is_builtin": False,
            }
        )
    return out


def create_style(db: Session, tenant_id: str, user_id: int, title: str, prompt_text: str) -> UserNarrationStyle:
    row = UserNarrationStyle(
        tenant_id=tenant_id,
        user_id=user_id,
        title=title.strip()[:200],
        prompt_text=prompt_text.strip()[:12000],
    )
    db.add(row)
    db.flush()
    log.info("narration_style_created", style_id=str(row.id), tenant_id=tenant_id)
    return row


def get_owned_style(db: Session, tenant_id: str, user_id: int, style_id: uuid.UUID) -> UserNarrationStyle | None:
    return db.scalars(
        select(UserNarrationStyle).where(
            UserNarrationStyle.id == style_id,
            UserNarrationStyle.tenant_id == tenant_id,
            UserNarrationStyle.user_id == user_id,
        )
    ).first()


def patch_style(
    db: Session,
    tenant_id: str,
    user_id: int,
    style_id: uuid.UUID,
    *,
    title: str | None,
    prompt_text: str | None,
) -> UserNarrationStyle | None:
    row = get_owned_style(db, tenant_id, user_id, style_id)
    if not row:
        return None
    if title is not None:
        row.title = title.strip()[:200]
    if prompt_text is not None:
        row.prompt_text = prompt_text.strip()[:12000]
    db.flush()
    log.info("narration_style_patched", style_id=str(style_id), tenant_id=tenant_id)
    return row


def delete_style(db: Session, tenant_id: str, user_id: int, style_id: uuid.UUID) -> bool:
    row = get_owned_style(db, tenant_id, user_id, style_id)
    if not row:
        return False
    db.delete(row)
    db.flush()
    log.info("narration_style_deleted", style_id=str(style_id), tenant_id=tenant_id)
    return True
