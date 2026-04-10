"""Aggregated pipeline progress for studio UI (manual vs auto)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import Chapter, Project, ProjectCharacter, ResearchDossier, Scene, TimelineVersion
from director_api.services.agent_resume import workflow_phase_rank
from director_api.services.phase5_readiness import compute_phase5_readiness, scene_image_video_counts, scenes_spoken_narration_coverage
from ffmpeg_pipelines.paths import path_is_readable_file


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
    scenes_done = scenes_tot > 0
    characters_done = int(char_n) > 0
    critique_blocked = p.workflow_phase == "critique_review"

    steps: list[dict[str, Any]] = [
        {"id": "director", "label": "Directely pack", "status": st(director_done)},
        {"id": "research", "label": "Research & dossier", "status": st(research_done, critique_blocked)},
        {"id": "outline", "label": "Chapter outline", "status": st(outline_done)},
        {"id": "chapters", "label": "Chapter scripts", "status": st(chapters_done)},
        {"id": "scenes", "label": "Scene planning", "status": st(scenes_done)},
        {
            "id": "characters",
            "label": "Character bible",
            "status": st(characters_done),
            "detail": f"{int(char_n)} character(s)" if int(char_n) else "—",
        },
        {
            "id": "images",
            "label": "Scene images",
            "status": "done" if scenes_tot > 0 and scenes_img >= scenes_tot else "pending",
            "detail": f"{scenes_img}/{scenes_tot} with image",
        },
        {
            "id": "video_clips",
            "label": "Scene videos (optional)",
            "status": "pending",
            "detail": f"{scenes_vid}/{scenes_tot} with video",
        },
        {
            "id": "narration",
            "label": "Scene narration (TTS)",
            "status": "done" if narr_need > 0 and narr_ok >= narr_need else "pending",
            "detail": f"{narr_ok}/{narr_need} scenes",
        },
        {
            "id": "timeline",
            "label": "Timeline version",
            "status": "done" if latest_tv else "pending",
            "detail": tv_id or "—",
        },
        {"id": "rough_cut", "label": "Rough cut MP4", "status": "done" if rough_ok else "pending"},
        {"id": "final_cut", "label": "Final cut (narration mux)", "status": "done" if final_ok else "pending"},
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
