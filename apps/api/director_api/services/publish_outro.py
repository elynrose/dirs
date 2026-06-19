"""Optional subscribe outro as the last scene in the final chapter."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.agents.phase2_publish_llm import generate_outro_cta_llm
from director_api.config import Settings
from director_api.db.models import Chapter, Project, Scene
from director_api.services.phase3 import default_scene_negative_prompt_for_project
from director_api.services.research_service import sanitize_jsonb_text
from director_api.style_presets import effective_narration_style, effective_visual_style

log = structlog.get_logger(__name__)

OUTRO_SCENE_ROLE = "outro"
DEFAULT_OUTRO_NARRATION = (
    "If you enjoyed this video, please subscribe and hit the bell so you don't miss the next one. "
    "Thanks for watching."
)


def resolve_include_outro_scene(project: Project, pipeline_options: dict[str, Any] | None) -> bool:
    if isinstance(pipeline_options, dict) and pipeline_options.get("include_outro_scene") is not None:
        return bool(pipeline_options.get("include_outro_scene"))
    return bool(getattr(project, "include_outro_scene", False))


def find_outro_scene(db: Session, project_id: uuid.UUID) -> Scene | None:
    rows = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index.desc(), Scene.order_index.desc())
        ).all()
    )
    for sc in rows:
        pp = sc.prompt_package_json if isinstance(sc.prompt_package_json, dict) else {}
        if pp.get("scene_role") == OUTRO_SCENE_ROLE:
            return sc
    return None


def _last_chapter(db: Session, project_id: uuid.UUID) -> Chapter | None:
    return db.scalars(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index.desc()).limit(1)
    ).first()


def append_outro_scene(
    db: Session,
    project: Project,
    settings: Settings,
    *,
    narration_override: str | None = None,
) -> Scene:
    ch = _last_chapter(db, project.id)
    if not ch:
        raise ValueError("no chapters — run outline before outro")
    existing = find_outro_scene(db, project.id)
    if existing:
        db.delete(existing)
        db.flush()
    max_idx = db.scalar(select(func.max(Scene.order_index)).where(Scene.chapter_id == ch.id)) or -1
    nar_style = effective_narration_style(project.narration_style, settings, db=db, tenant_id=project.tenant_id)
    narration = (narration_override or "").strip()
    if len(narration) < 12:
        llm_u: list[dict[str, Any]] = []
        generated = generate_outro_cta_llm(
            project_title=project.title,
            narration_style=nar_style,
            settings=settings,
            usage_sink=llm_u,
        )
        narration = generated or DEFAULT_OUTRO_NARRATION
        if llm_u:
            from director_api.tasks.worker_runtime import _flush_llm_usage

            _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
    narration = sanitize_jsonb_text(narration, 4000)
    vis = effective_visual_style(project.visual_style, settings)
    purpose = "Subscribe and thank viewers"
    pp: dict[str, Any] = {
        "scene_role": OUTRO_SCENE_ROLE,
        "image_prompt": sanitize_jsonb_text(
            f"Clean end card, documentary style, {vis}. Simple subscribe call-to-action visual, "
            "warm lighting, no cluttered text in image.",
            4000,
        ),
        "video_prompt": sanitize_jsonb_text(
            f"Gentle closing shot, documentary outro, {vis}.",
            3000,
        ),
        "negative_prompt": default_scene_negative_prompt_for_project(project, None),
    }
    sc = Scene(
        id=uuid.uuid4(),
        chapter_id=ch.id,
        order_index=int(max_idx) + 1,
        purpose=purpose,
        narration_text=narration,
        visual_type="still",
        prompt_package_json=pp,
        continuity_tags_json=["outro", "cta"],
        status="draft",
    )
    db.add(sc)
    project.workflow_phase = "outro_ready"
    db.flush()
    return sc


def remove_outro_scene(db: Session, project_id: uuid.UUID) -> bool:
    sc = find_outro_scene(db, project_id)
    if not sc:
        return False
    db.delete(sc)
    db.flush()
    return True
