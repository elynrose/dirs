"""Manual Step-by-step import: chapter + one scene per line (paste or .txt upload)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import Chapter, Project, Scene
from director_api.services import agent_resume as agent_resume_svc
from director_api.services import phase3 as phase3_svc
from director_api.services.character_prompt import load_project_character_bible_chunks
from director_api.services.erase_consent import assert_chapter_replan_erase_consent
from director_api.services.research_service import sanitize_jsonb_text
from director_api.validation.phase3_schemas import validate_scene_plan_batch


def _next_chapter_order_index(db: Session, project_id: uuid.UUID) -> int:
    mx = db.scalar(
        select(func.max(Chapter.order_index)).where(Chapter.project_id == project_id)
    )
    return int(mx or -1) + 1


def _persist_scene_plan_batch(
    db: Session,
    chapter: Chapter,
    project: Project,
    batch: dict[str, Any],
    *,
    replace_existing: bool,
    confirm_erase_assets: bool,
) -> int:
    n_existing = int(
        db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == chapter.id)) or 0
    )
    if n_existing > 0:
        if not replace_existing:
            raise ValueError(
                f"SCENES_ALREADY_PLANNED: chapter already has {n_existing} scene(s); "
                "set replace_existing_scenes=true to replace them"
            )
        assert_chapter_replan_erase_consent(chapter, consent=confirm_erase_assets)
        for sc in list(chapter.scenes):
            db.delete(sc)
        db.flush()

    validate_scene_plan_batch(batch)
    for item in sorted(batch["scenes"], key=lambda x: int(x["order_index"])):
        pp = item.get("prompt_package_json")
        if not isinstance(pp, dict):
            pp = {}
        for pref_key in ("preferred_image_provider", "preferred_video_provider"):
            if item.get(pref_key):
                pp[f"_{pref_key}"] = str(item[pref_key])[:64]
        phase3_svc.merge_stock_search_terms_from_plan_row(item, pp)
        ct = item.get("continuity_tags_json")
        if not isinstance(ct, list):
            ct = []
        ct = [str(x)[:256] for x in ct if x is not None][:32]
        narr_out = str(item["narration_text"])
        if getattr(project, "no_narration", False):
            narr_out = phase3_svc.NO_NARRATION_SCENE_TEXT
        db.add(
            Scene(
                id=uuid.uuid4(),
                chapter_id=chapter.id,
                order_index=int(item["order_index"]),
                purpose=sanitize_jsonb_text(str(item["purpose"]), 2000),
                planned_duration_sec=int(item["planned_duration_sec"]),
                narration_text=sanitize_jsonb_text(narr_out, 12_000),
                visual_type=str(item["visual_type"])[:64],
                prompt_package_json=pp,
                continuity_tags_json=ct,
                status="planned",
            )
        )
    if agent_resume_svc.all_scripted_chapters_have_scenes(db, project):
        project.workflow_phase = "scenes_planned"
    elif project.workflow_phase in ("draft", "director_ready", "research_running", "research_approved", "outline_ready"):
        project.workflow_phase = "chapters_ready"
    db.flush()
    return len(batch.get("scenes") or [])


def create_chapter_with_manual_scenes(
    db: Session,
    project: Project,
    *,
    title: str,
    text: str,
    summary: str | None = None,
    target_duration_sec: int | None = None,
) -> tuple[Chapter, int]:
    lines = phase3_svc.parse_manual_scene_lines(text)
    if not lines:
        raise ValueError("MANUAL_SCENE_LINES_REQUIRED: at least one non-empty line is required")

    order_index = _next_chapter_order_index(db, project.id)
    chapter = Chapter(
        id=uuid.uuid4(),
        project_id=project.id,
        order_index=order_index,
        title=sanitize_jsonb_text(title.strip(), 500),
        summary=sanitize_jsonb_text(summary, 8000) if summary else None,
        target_duration_sec=target_duration_sec,
        script_text=sanitize_jsonb_text(text.strip(), 120_000),
        status="draft",
    )
    db.add(chapter)
    db.flush()

    char_chunks = load_project_character_bible_chunks(db, project.id)
    batch = phase3_svc.build_scene_plan_batch_from_lines(
        chapter, project, lines, character_bible_chunks=char_chunks
    )
    scene_count = _persist_scene_plan_batch(
        db, chapter, project, batch, replace_existing=False, confirm_erase_assets=False
    )
    return chapter, scene_count


def import_manual_scenes_to_chapter(
    db: Session,
    chapter: Chapter,
    project: Project,
    *,
    text: str,
    replace_existing_scenes: bool = True,
    confirm_erase_assets: bool = False,
    update_script_text: bool = True,
) -> int:
    lines = phase3_svc.parse_manual_scene_lines(text)
    if not lines:
        raise ValueError("MANUAL_SCENE_LINES_REQUIRED: at least one non-empty line is required")

    if update_script_text:
        chapter.script_text = sanitize_jsonb_text(text.strip(), 120_000)

    char_chunks = load_project_character_bible_chunks(db, project.id)
    batch = phase3_svc.build_scene_plan_batch_from_lines(
        chapter, project, lines, character_bible_chunks=char_chunks
    )
    return _persist_scene_plan_batch(
        db,
        chapter,
        project,
        batch,
        replace_existing=replace_existing_scenes,
        confirm_erase_assets=confirm_erase_assets,
    )
