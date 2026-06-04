"""Erase-consent gates before outline/scene replans that delete generated media."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import Asset, Chapter, Project, Scene
from director_api.services.pipeline_oversight import parse_force_pipeline_steps

_OUTLINE_STEPS = frozenset({"outline", "chapters"})
_SCENE_STEPS = frozenset({"scenes"})


@dataclass
class EraseScope:
    scene_count: int = 0
    image_asset_count: int = 0
    video_asset_count: int = 0
    chapter_count: int = 0

    @property
    def has_content_to_erase(self) -> bool:
        return (self.scene_count + self.image_asset_count + self.video_asset_count) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_count": self.scene_count,
            "image_asset_count": self.image_asset_count,
            "video_asset_count": self.video_asset_count,
            "chapter_count": self.chapter_count,
            "has_content_to_erase": self.has_content_to_erase,
        }


class EraseConfirmationRequired(Exception):
    def __init__(self, *, scope_label: str, scope: EraseScope) -> None:
        self.scope_label = scope_label
        self.scope = scope
        super().__init__(f"erase confirmation required ({scope_label})")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": "ERASE_CONFIRMATION_REQUIRED",
            "scope_label": self.scope_label,
            "scope": self.scope.to_dict(),
        }


def options_grant_erase_consent(opts: dict[str, Any] | None) -> bool:
    if not isinstance(opts, dict):
        return False
    return bool(opts.get("confirm_erase_assets"))


def pipeline_options_imply_outline_wipe(opts: dict[str, Any] | None) -> bool:
    if not isinstance(opts, dict):
        return False
    force = parse_force_pipeline_steps(opts)
    if force & _OUTLINE_STEPS:
        return True
    step = str(opts.get("rerun_from_step") or "").strip().lower()
    return step in _OUTLINE_STEPS


def pipeline_options_imply_scenes_wipe(opts: dict[str, Any] | None) -> bool:
    if not isinstance(opts, dict):
        return False
    if bool(opts.get("force_replan_scenes")):
        return True
    force = parse_force_pipeline_steps(opts)
    if force & _SCENE_STEPS:
        return True
    step = str(opts.get("rerun_from_step") or "").strip().lower()
    return step in _SCENE_STEPS


def _count_visual_assets_for_project(db: Session, project_id) -> tuple[int, int]:
    img = db.scalar(
        select(func.count())
        .select_from(Asset)
        .where(
            Asset.project_id == project_id,
            Asset.asset_type.in_(("image", "still")),
        )
    ) or 0
    vid = db.scalar(
        select(func.count())
        .select_from(Asset)
        .where(
            Asset.project_id == project_id,
            Asset.asset_type.in_(("video", "clip")),
        )
    ) or 0
    return int(img), int(vid)


def compute_outline_erase_scope(project: Project) -> EraseScope:
    db = Session.object_session(project)
    if db is None:
        return EraseScope()
    chapter_count = db.scalar(
        select(func.count()).select_from(Chapter).where(Chapter.project_id == project.id)
    ) or 0
    scene_count = db.scalar(
        select(func.count())
        .select_from(Scene)
        .join(Chapter, Scene.chapter_id == Chapter.id)
        .where(Chapter.project_id == project.id)
    ) or 0
    img, vid = _count_visual_assets_for_project(db, project.id)
    return EraseScope(
        scene_count=int(scene_count),
        image_asset_count=img,
        video_asset_count=vid,
        chapter_count=int(chapter_count),
    )


def compute_project_replan_erase_scope(project: Project) -> EraseScope:
    return compute_outline_erase_scope(project)


def _chapter_erase_scope(db: Session, chapter_id) -> EraseScope:
    scene_count = db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == chapter_id)) or 0
    img = db.scalar(
        select(func.count())
        .select_from(Asset)
        .join(Scene, Asset.scene_id == Scene.id)
        .where(Scene.chapter_id == chapter_id, Asset.asset_type.in_(("image", "still")))
    ) or 0
    vid = db.scalar(
        select(func.count())
        .select_from(Asset)
        .join(Scene, Asset.scene_id == Scene.id)
        .where(Scene.chapter_id == chapter_id, Asset.asset_type.in_(("video", "clip")))
    ) or 0
    return EraseScope(scene_count=int(scene_count), image_asset_count=int(img), video_asset_count=int(vid))


def assert_chapter_replan_erase_consent(chapter: Chapter, *, consent: bool) -> None:
    db = Session.object_session(chapter)
    if db is None:
        return
    scope = _chapter_erase_scope(db, chapter.id)
    if scope.has_content_to_erase and not consent:
        raise EraseConfirmationRequired(scope_label="chapter_scenes_replan", scope=scope)


def assert_outline_erase_consent(project: Project, *, consent: bool) -> None:
    """Outline regen deletes all chapters (and cascaded scenes/assets)."""
    scope = compute_outline_erase_scope(project)
    if scope.has_content_to_erase and not consent:
        raise EraseConfirmationRequired(scope_label="outline", scope=scope)
