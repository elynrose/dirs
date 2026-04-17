"""Batch-load timeline manifest rows (Asset → Scene → Chapter) to avoid N+1 queries."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from director_api.db.models import Asset, Chapter, Scene


def manifest_prefetch_asset_hierarchy(
    db: Any,
    manifest: list[dict[str, Any]],
) -> tuple[dict[uuid.UUID, Asset], dict[uuid.UUID, Scene], dict[uuid.UUID, Chapter]]:
    """Batch-load Asset → Scene → Chapter for all manifest rows."""
    aids: set[uuid.UUID] = set()
    for m in manifest:
        aid = m.get("asset_id")
        if aid is None:
            continue
        try:
            aids.add(uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            continue
    if not aids:
        return {}, {}, {}
    assets = list(db.scalars(select(Asset).where(Asset.id.in_(aids))).all())
    asset_by_id = {a.id: a for a in assets}
    scene_ids = {a.scene_id for a in assets if a.scene_id}
    if not scene_ids:
        return asset_by_id, {}, {}
    scenes = list(db.scalars(select(Scene).where(Scene.id.in_(scene_ids))).all())
    scene_by_id = {s.id: s for s in scenes}
    ch_ids = {s.chapter_id for s in scenes if s.chapter_id}
    if not ch_ids:
        return asset_by_id, scene_by_id, {}
    chapters = list(db.scalars(select(Chapter).where(Chapter.id.in_(ch_ids))).all())
    ch_by_id = {c.id: c for c in chapters}
    return asset_by_id, scene_by_id, ch_by_id
