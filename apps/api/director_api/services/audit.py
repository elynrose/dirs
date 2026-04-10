"""Append-only audit trail (Phase 6 baseline)."""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import AuditEvent


def record_audit(
    db: Session,
    settings: Settings,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    actor_id: str | None,
    payload_summary: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=settings.default_tenant_id,
            actor_id=(actor_id or "")[:256] or None,
            action=action[:128],
            resource_type=resource_type[:64],
            resource_id=resource_id,
            payload_summary=(payload_summary or "")[:4000] or None,
        )
    )
