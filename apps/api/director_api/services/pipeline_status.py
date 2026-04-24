"""Aggregated pipeline progress for studio UI (manual vs auto).

Each step includes ``label`` (title shown in the inspector) and ``detail`` (secondary line):

- Counts use ``{done}/{total} · {unit}`` (e.g. ``7/12 · images``) or ``{n} · {plural_unit}``.
- Readiness without a numeric breakdown uses words like ``Complete`` / ``Pending``, or filenames when an export exists.
- Use ``—`` when there is nothing useful to show yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import (
    Chapter,
    CriticReport,
    Project,
    ProjectCharacter,
    ResearchDossier,
    TimelineVersion,
)
from director_api.services.agent_resume import workflow_phase_rank
from director_api.services.phase5_readiness import compute_phase5_readiness, scene_image_video_counts, scenes_spoken_narration_coverage
from ffmpeg_pipelines.paths import path_is_readable_file

# Detail line convention for Studio: ``—`` when nothing to report; counts use ``{done}/{total} · {unit}``;
# single counts use ``{n} · {unit}`` (plural unit word).
_EM = "—"


def _detail_frac(done: int, total: int, unit: str) -> str:
    if int(total) <= 0:
        return _EM
    return f"{int(done)}/{int(total)} · {unit}"


def _detail_count(n: int, unit_singular: str, unit_plural: str | None = None) -> str:
    if int(n) <= 0:
        return _EM
    u = unit_singular if int(n) == 1 else (unit_plural or f"{unit_singular}s")
    return f"{int(n)} · {u}"


def _story_research_review_done(db: Session, project_id: UUID) -> bool:
    """True when the worker's one-shot story-vs-research critic row exists (same latch as agent tail)."""
    n = db.scalar(
        select(func.count())
        .select_from(CriticReport)
        .where(
            CriticReport.project_id == project_id,
            CriticReport.target_type == "project",
            CriticReport.target_id == project_id,
            CriticReport.meta_json.isnot(None),
            CriticReport.meta_json["kind"].astext == "story_research_review",
        )
    )
    return int(n or 0) > 0


def compute_pipeline_status(
    db: Session,
    *,
    project_id: UUID,
    tenant_id: str,
    storage_root: str | Path | None = None,
) -> dict[str, Any]:
    p = db.get(Project, project_id)
    if not p or p.tenant_id != tenant_id:
        return {"ok": False, "error": "project_not_found"}

    rank = workflow_phase_rank(p.workflow_phase)
    chapters_n = db.scalar(select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)) or 0
    dossier_n = db.scalar(select(func.count()).select_from(ResearchDossier).where(ResearchDossier.project_id == project_id)) or 0

    scenes_tot, scenes_img, scenes_vid, _scenes_appr = scene_image_video_counts(db, project_id)
    char_n = (
        db.scalar(select(func.count()).select_from(ProjectCharacter).where(ProjectCharacter.project_id == project_id))
        or 0
    )
    narr_need, narr_ok = scenes_spoken_narration_coverage(db, project_id)

    tv_rows = list(
        db.scalars(
            select(TimelineVersion)
            .where(TimelineVersion.project_id == project_id, TimelineVersion.tenant_id == tenant_id)
            .order_by(TimelineVersion.created_at.desc())
            .limit(3)
        ).all()
    )
    latest_tv = tv_rows[0] if tv_rows else None
    tv_id = str(latest_tv.id) if latest_tv else None
    rough_ok = final_ok = False
    if latest_tv and storage_root:
        root = Path(storage_root).resolve()
        base = root / "exports" / str(project_id) / str(latest_tv.id)
        rough_ok = path_is_readable_file(base / "rough_cut.mp4")
        final_ok = path_is_readable_file(base / "final_cut.mp4")

    readiness = compute_phase5_readiness(
        db, project_id=project_id, tenant_id=tenant_id, storage_root=storage_root
    )

    def st(done: bool, blocked: bool = False) -> str:
        if blocked:
            return "blocked"
        return "done" if done else "pending"

    director_done = p.director_output_json is not None
    research_done = rank >= 3 and int(dossier_n) > 0
    outline_done = rank >= 4
    chapters_done = rank >= 5
    scenes_done = rank >= 6 or scenes_tot > 0
    story_research_done = _story_research_review_done(db, project_id)
    characters_done = int(char_n) > 0
    critique_blocked = p.workflow_phase == "critique_review"

    chapters_detail = _detail_count(int(chapters_n), "chapter", "chapters")
    scenes_detail = _detail_count(scenes_tot, "scene", "scenes")
    research_detail = _detail_count(int(dossier_n), "dossier", "dossiers")
    story_detail = "Complete" if story_research_done else "Pending"
    char_detail = _detail_count(int(char_n), "character", "characters")
    images_detail = _detail_frac(scenes_img, scenes_tot, "images")
    videos_detail = _detail_frac(scenes_vid, scenes_tot, "videos")
    narr_detail = _detail_frac(narr_ok, narr_need, "narration") if int(narr_need) > 0 else _EM
    timeline_detail = f"{str(tv_id)[:8]}…" if tv_id else _EM
    rough_detail = "rough_cut.mp4" if rough_ok else _EM
    final_detail = "final_cut.mp4" if final_ok else _EM

    steps: list[dict[str, Any]] = [
        {"id": "director", "label": "Directely pack", "status": st(director_done), "detail": _EM},
        {
            "id": "research",
            "label": "Research & dossier",
            "status": st(research_done, critique_blocked),
            "detail": research_detail,
        },
        {"id": "outline", "label": "Chapter outline", "status": st(outline_done), "detail": _EM},
        {"id": "chapters", "label": "Chapter scripts", "status": st(chapters_done), "detail": chapters_detail},
        {"id": "scenes", "label": "Scene planning", "status": st(scenes_done), "detail": scenes_detail},
        {
            "id": "story_research_review",
            "label": "Story vs research",
            "status": st(story_research_done),
            "detail": story_detail,
        },
        {
            "id": "characters",
            "label": "Character bible",
            "status": st(characters_done),
            "detail": char_detail,
        },
        {
            "id": "narration",
            "label": "Scene narration (TTS)",
            "status": "done" if narr_need > 0 and narr_ok >= narr_need else "pending",
            "detail": narr_detail,
        },
        {
            "id": "images",
            "label": "Scene images",
            "status": "done" if scenes_tot > 0 and scenes_img >= scenes_tot else "pending",
            "detail": images_detail,
        },
        {
            "id": "video_clips",
            "label": "Scene videos (optional)",
            "status": "done" if scenes_tot > 0 and scenes_vid >= scenes_tot else "pending",
            "detail": videos_detail,
        },
        {
            "id": "timeline",
            "label": "Timeline version",
            "status": "done" if latest_tv else "pending",
            "detail": timeline_detail,
        },
        {
            "id": "rough_cut",
            "label": "Rough cut",
            "status": "done" if rough_ok else "pending",
            "detail": rough_detail,
        },
        {
            "id": "final_cut",
            "label": "Final mix",
            "status": "done" if final_ok else "pending",
            "detail": final_detail,
        },
    ]

    return {
        "ok": True,
        "project_id": str(project_id),
        "workflow_phase": p.workflow_phase,
        "phase_rank": rank,
        "chapter_count": int(chapters_n),
        "scene_count": scenes_tot,
        "phase5_ready": bool(readiness.get("ready")),
        "phase5_issues": readiness.get("issues") or [],
        "latest_timeline_version_id": tv_id,
        "steps": steps,
    }
