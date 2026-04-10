"""Shared tenant-scoped resource guards (avoid inconsistent 404 vs empty-list behavior)."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from director_api.db.models import Project


def get_project_for_tenant(db: Session, project_id: UUID, tenant_id: str) -> Project | None:
    """Return the project if it exists and belongs to ``tenant_id``, else ``None``."""
    p = db.get(Project, project_id)
    if not p or p.tenant_id != tenant_id:
        return None
    return p


def require_project_for_tenant(db: Session, project_id: UUID, tenant_id: str) -> Project:
    """404 when missing or wrong tenant (do not reveal cross-tenant existence)."""
    p = get_project_for_tenant(db, project_id, tenant_id)
    if not p:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "project not found"},
        )
    return p
