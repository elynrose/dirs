"""Resume / skip rules for autonomous agent runs (continue_from_existing)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import Chapter, Project, ResearchDossier, Scene


def workflow_phase_rank(phase: str | None) -> int:
    return {
        "draft": 0,
        "director_ready": 1,
        "research_running": 1,
        "research_ready": 2,
        "research_approved": 3,
        "outline_ready": 4,
        "chapters_ready": 5,
        "scenes_planned": 6,
        "critique_review": 7,
        "critique_complete": 8,
        "final_video_ready": 9,
    }.get((phase or "draft").strip(), 0)


def latest_dossier(db: Session, project_id: UUID) -> ResearchDossier | None:
    return db.scalars(
        select(ResearchDossier)
        .where(ResearchDossier.project_id == project_id)
        .order_by(ResearchDossier.version.desc())
        .limit(1)
    ).first()


def should_skip_director(continue_existing: bool, project: Project) -> bool:
    if not continue_existing:
        return False
    return project.director_output_json is not None


def should_skip_research(continue_existing: bool, project: Project, db: Session) -> bool:
    """Skip web research when resuming if a dossier already exists (do not repeat by default)."""
    if not continue_existing:
        return False
    return latest_dossier(db, project.id) is not None


def should_skip_outline(continue_existing: bool, project: Project) -> bool:
    if not continue_existing:
        return False
    return workflow_phase_rank(project.workflow_phase) >= 4


def _all_chapters_have_substantive_scripts(db: Session, project: Project, *, min_chars: int = 200) -> bool:
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)).all()
    )
    if not chapters:
        return False
    for ch in chapters:
        if len((ch.script_text or "").strip()) < min_chars:
            return False
    return True


def should_skip_chapters(continue_existing: bool, project: Project, db: Session | None = None) -> bool:
    if not continue_existing:
        return False
    if workflow_phase_rank(project.workflow_phase) >= 5:
        return True
    if db is not None and _all_chapters_have_substantive_scripts(db, project):
        return True
    return False


def all_scripted_chapters_have_scenes(db: Session, project: Project) -> bool:
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)).all()
    )
    for ch in chapters:
        if len((ch.script_text or "").strip()) < 12:
            continue
        n = db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch.id)) or 0
        if int(n) == 0:
            return False
    return True


def should_skip_scenes_plan(
    continue_existing: bool,
    project: Project,
    db: Session,
    *,
    through: str = "critique",
    force_replan_scenes: bool = False,
) -> bool:
    """
    Skip the entire scenes step only when resuming and work is already done.

    For ``through == "full_video"``, do not skip just because every scripted chapter already has
    at least one scene — that hides under-planned chapters (e.g. a single scene per long script).
    The per-chapter loop still decides skip vs replan.
    """
    if not continue_existing:
        return False
    if force_replan_scenes:
        return False
    if workflow_phase_rank(project.workflow_phase) >= 6:
        return True
    if str(through or "").strip().lower() == "full_video":
        return False
    return all_scripted_chapters_have_scenes(db, project)


def should_skip_agent_replan_chapter_scenes(
    continue_from_existing: bool,
    force_replan_scenes: bool,
    scene_row_count: int,
    *,
    through: str = "critique",
    script_text: str | None = None,
) -> bool:
    """When resuming the agent, keep chapters that already have a scene plan unless force_replan_scenes.

    Automate must not wipe existing scene rows to \"fix\" under-planning; use the scene planner in the
    UI or force the scenes step when you intentionally want a full replan.
    """
    _ = (through, script_text)  # kept for API compatibility with callers/tests
    if not continue_from_existing or force_replan_scenes:
        return False
    return int(scene_row_count) > 0


def agent_scenes_chapter_planning_action(
    script_text: str | None,
    continue_from_existing: bool,
    force_replan_scenes: bool,
    scene_row_count: int,
    *,
    through: str = "critique",
    oversight_force_scenes: bool = False,
) -> Literal["short_script", "skip_existing_scenes", "plan"]:
    """What the agent scenes step should do for one chapter (mirrors worker loop)."""
    if len((script_text or "").strip()) < 12:
        return "short_script"
    if oversight_force_scenes:
        return "plan"
    if should_skip_agent_replan_chapter_scenes(
        continue_from_existing,
        force_replan_scenes,
        int(scene_row_count),
        through=through,
        script_text=script_text,
    ):
        return "skip_existing_scenes"
    return "plan"


def should_skip_story_research_review(continue_existing: bool, project: Project) -> bool:
    """
    Legacy helper: the worker no longer uses this for routing.

    Story vs research runs **once** per project after scene planning (worker), then is skipped when a
    project-level critic row with ``meta.kind == story_research_review`` already exists.
    """
    if not continue_existing:
        return False
    if project.workflow_phase == "critique_review":
        return False
    return workflow_phase_rank(project.workflow_phase) >= 8


def should_skip_scene_critique(continue_existing: bool, project: Project) -> bool:
    """Deprecated: use should_skip_story_research_review (kept for older callers)."""
    return should_skip_story_research_review(continue_existing, project)


def should_skip_chapter_critique(continue_existing: bool, project: Project) -> bool:
    """Deprecated: use should_skip_story_research_review (kept for older callers)."""
    return should_skip_story_research_review(continue_existing, project)


def parse_pipeline_options(raw: Any) -> tuple[bool, str, bool]:
    """
    Returns (continue_from_existing, through, unattended).

    ``unattended`` relaxes the strict research gate so a run can continue without a human
    fixing dossier/source counts (logged as warnings). Hands-off / unattended runs are always
    intended to reach **final video**; if ``through`` is missing or wrongly set to ``critique``,
    we coerce to ``full_video`` (``chapters`` is preserved when explicitly requested).
    """
    if not isinstance(raw, dict):
        return False, "critique", False
    cont = bool(raw.get("continue_from_existing"))
    unattended = bool(raw.get("unattended"))
    # Unattended defaults to full depth; critique-only default applies to attended Auto runs.
    default_through = "full_video" if unattended else "critique"
    through = str(raw.get("through") or default_through).strip().lower()
    # chapters = stop after chapter scripts (manual “new project + run”); critique / full_video continue automation.
    if through not in ("critique", "full_video", "chapters"):
        through = default_through
    # Stale clients or merged options sometimes send unattended + critique; that stops after story review.
    if unattended and through == "critique":
        through = "full_video"
    return cont, through, unattended


def normalize_pipeline_options_for_persist(raw: dict[str, Any]) -> dict[str, Any]:
    """Copy ``raw`` with canonical ``through`` / ``continue_from_existing`` / ``unattended`` for DB storage.

    Ensures stored JSON matches what :func:`parse_pipeline_options` will apply in workers (so API responses
    and retries show the effective depth, not a stale ``through: critique`` with ``unattended: true``).
    """

    base = dict(raw) if isinstance(raw, dict) else {}
    cont, through, unattended = parse_pipeline_options(base)
    return {
        **base,
        "continue_from_existing": cont,
        "through": through,
        "unattended": unattended,
    }
