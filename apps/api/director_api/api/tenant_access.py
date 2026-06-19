"""Shared tenant-scoped resource guards (avoid inconsistent 404 vs empty-list behavior)."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from director_api.db.models import Asset, Chapter, Project, Scene


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


def get_chapter_for_tenant(db: Session, chapter_id: UUID, tenant_id: str) -> Chapter | None:
    ch = db.get(Chapter, chapter_id)
    if not ch:
        return None
    return ch if get_project_for_tenant(db, ch.project_id, tenant_id) else None


def require_chapter_for_tenant(db: Session, chapter_id: UUID, tenant_id: str) -> Chapter:
    ch = get_chapter_for_tenant(db, chapter_id, tenant_id)
    if not ch:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "chapter not found"},
        )
    return ch


def get_scene_for_tenant(db: Session, scene_id: UUID, tenant_id: str) -> Scene | None:
    sc = db.get(Scene, scene_id)
    if not sc:
        return None
    return sc if get_chapter_for_tenant(db, sc.chapter_id, tenant_id) else None


def require_scene_for_tenant(db: Session, scene_id: UUID, tenant_id: str) -> Scene:
    sc = get_scene_for_tenant(db, scene_id, tenant_id)
    if not sc:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "scene not found"},
        )
    return sc


def require_asset_for_tenant(db: Session, asset_id: UUID, tenant_id: str) -> Asset:
    a = db.get(Asset, asset_id)
    if not a or a.tenant_id != tenant_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "asset not found"},
        )
    return a
