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
from director_api.services.scene_coverage import project_scene_coverage_counts
from director_api.services.scene_timeline_duration import scene_vo_tail_padding_sec_from_settings
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


def _clip_sec_from_settings(settings: Any) -> float:
    try:
        v = int(getattr(settings, "scene_clip_duration_sec", 10) or 10)
    except (TypeError, ValueError):
        v = 10
    return 5.0 if v == 5 else 10.0


def compute_pipeline_status(
    db: Session,
    *,
    project_id: UUID,
    tenant_id: str,
    storage_root: str | Path | None = None,
    settings: Any | None = None,
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
    from director_api.services.publish_pack import publish_pack_done

    thumbnail_done = publish_pack_done(p.publish_pack_json)
    from director_api.services.publish_hook import find_hook_scene

    hook_sc = find_hook_scene(db, project_id)
    hook_text_ok = len((p.opening_hook_text or "").strip()) >= 12
    hook_done = hook_sc is not None or hook_text_ok or rank >= 7
    scenes_done = rank >= 8 or scenes_tot > 0
    from director_api.services.publish_outro import find_outro_scene

    outro_sc = find_outro_scene(db, project_id)
    outro_done = outro_sc is not None or rank >= 9
    if not getattr(p, "include_outro_scene", False):
        outro_status = "skipped"
    else:
        outro_status = st(outro_done)
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

    coverage_enabled = bool(
        getattr(p, "use_all_approved_scene_media", False)
        or (settings is not None and getattr(settings, "agent_run_auto_scene_coverage_clips", False))
    )
    auto_videos_enabled = settings is None or getattr(settings, "agent_run_auto_generate_scene_videos", True) is not False

    if coverage_enabled and scenes_tot > 0 and settings is not None:
        clip_sec = _clip_sec_from_settings(settings)
        tail_pad = scene_vo_tail_padding_sec_from_settings(settings)
        ffprobe_bin = (getattr(settings, "ffprobe_bin", None) or "ffprobe").strip() or "ffprobe"
        timeout_sec = float(getattr(settings, "ffmpeg_timeout_sec", 120) or 120)
        _cov_tot, cov_met, slots_have, slots_need = project_scene_coverage_counts(
            db,
            project_id,
            storage_root=storage_root,
            clip_sec=clip_sec,
            tail_padding_sec=tail_pad,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        coverage_status = "done" if cov_met >= scenes_tot and scenes_tot > 0 else "pending"
        if slots_need > scenes_tot:
            coverage_detail = f"{cov_met}/{scenes_tot} · scenes · {_detail_frac(slots_have, slots_need, 'clips')}"
        else:
            coverage_detail = _detail_frac(cov_met, scenes_tot, "scenes")
    elif coverage_enabled and scenes_tot > 0:
        coverage_status = "pending"
        coverage_detail = _EM
    elif coverage_enabled:
        coverage_status = "pending"
        coverage_detail = _EM
    else:
        coverage_status = "skipped"
        coverage_detail = "Off in Settings"

    if auto_videos_enabled:
        videos_status = "done" if scenes_tot > 0 and scenes_vid >= scenes_tot else "pending"
    else:
        videos_status = "skipped"
        videos_detail = "Off in Settings"

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
        {
            "id": "thumbnail",
            "label": "Thumbnail & YouTube copy",
            "status": st(thumbnail_done),
            "detail": _EM,
        },
        {
            "id": "opening_hook",
            "label": "The Hook",
            "status": st(hook_done),
            "detail": "Scene 0" if hook_sc else ("Ready" if hook_text_ok else _EM),
        },
        {"id": "scenes", "label": "Scene planning", "status": st(scenes_done), "detail": scenes_detail},
        {
            "id": "outro",
            "label": "Subscribe outro",
            "status": outro_status,
            "detail": "Last scene" if outro_done else _EM,
        },
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
            "id": "scene_coverage",
            "label": "Extra media pass",
            "status": coverage_status,
            "detail": coverage_detail,
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
            "status": videos_status,
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
