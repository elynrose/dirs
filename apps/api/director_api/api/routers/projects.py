import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.tenant_access import get_project_for_tenant, require_project_for_tenant
from director_api.db.session import get_db
from director_api.api.schemas.phase4 import CriticReportOut
from director_api.api.schemas.agent_run import AgentRunListItem
from director_api.api.schemas.project import JobOut, ProjectCreate, ProjectOut, ProjectPatch
from director_api.config import Settings, get_settings
from director_api.db.models import AgentRun, Asset, AuditEvent, Chapter, CriticReport, GenerationArtifact, Job, Project, Scene, TimelineVersion
from director_api.services import timeline_image_repair as timeline_image_repair_svc
from director_api.services.job_quota import assert_can_enqueue
from director_api.services.phase5_readiness import (
    build_phase5_gate_payload,
    compute_phase5_readiness,
    get_timeline_asset_for_project,
)
from director_api.tasks.job_enqueue import enqueue_run_phase3_job
from director_api.services.tenant_entitlements import assert_can_create_project
from director_api.storage.project_storage_cleanup import remove_generated_project_files
from director_api.services.project_frame import coerce_clip_frame_fit
from director_api.validation.brief import validate_documentary_brief

router = APIRouter(prefix="/projects", tags=["projects"])
log = structlog.get_logger(__name__)


def _timeline_version_or_404(db: Session, settings: Settings, timeline_version_id: UUID) -> TimelineVersion:
    tv = db.get(TimelineVersion, timeline_version_id)
    if not tv or tv.tenant_id != settings.default_tenant_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found"},
        )
    return tv


_BRIEF_FIELDS = (
    "title",
    "topic",
    "target_runtime_minutes",
    "audience",
    "tone",
    "visual_style",
    "narration_style",
    "factual_strictness",
    "music_preference",
    "preferred_text_provider",
    "preferred_image_provider",
    "preferred_video_provider",
    "preferred_speech_provider",
    "frame_aspect_ratio",
    "clip_frame_fit",
)


def _brief_dict_from_project(p: Project) -> dict[str, Any]:
    d: dict[str, Any] = {
        "title": p.title,
        "topic": p.topic,
        "target_runtime_minutes": p.target_runtime_minutes,
    }
    for k in _BRIEF_FIELDS[3:]:
        v = getattr(p, k, None)
        if v is not None:
            d[k] = v
    return d


@router.post("")
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    try:
        validate_documentary_brief(body.brief_dict())
    except Exception as e:  # noqa: BLE001
        log.warning("brief_validation_failed", error=str(e))
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(e)},
        ) from e
    assert_can_create_project(db, settings.default_tenant_id, auth_enabled=bool(get_settings().director_auth_enabled))
    p = Project(
        tenant_id=settings.default_tenant_id,
        title=body.title,
        topic=body.topic,
        status="draft",
        research_min_sources=body.research_min_sources if body.research_min_sources is not None else 3,
        target_runtime_minutes=body.target_runtime_minutes,
        audience=body.audience,
        tone=body.tone,
        visual_style=body.visual_style,
        narration_style=body.narration_style,
        factual_strictness=body.factual_strictness,
        music_preference=body.music_preference,
        budget_limit=body.budget_limit,
        preferred_text_provider=body.preferred_text_provider,
        preferred_image_provider=body.preferred_image_provider,
        preferred_video_provider=body.preferred_video_provider,
        preferred_speech_provider=body.preferred_speech_provider,
        frame_aspect_ratio=(body.frame_aspect_ratio or "16:9"),
        clip_frame_fit=coerce_clip_frame_fit(getattr(body, "clip_frame_fit", None)),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"data": ProjectOut.model_validate(p).model_dump(mode="json"), "meta": meta}


@router.get("")
def list_projects(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    n = max(1, min(int(limit), 200))
    off = max(0, int(offset))
    tenant = settings.default_tenant_id
    total = int(
        db.scalar(select(func.count()).select_from(Project).where(Project.tenant_id == tenant)) or 0
    )
    rows = list(
        db.scalars(
            select(Project)
            .where(Project.tenant_id == tenant)
            .order_by(desc(Project.updated_at), desc(Project.created_at))
            .offset(off)
            .limit(n)
        ).all()
    )
    project_ids = [p.id for p in rows]
    active_by_project: dict[uuid.UUID, AgentRun] = {}
    if project_ids:
        ar_rows = list(
            db.scalars(
                select(AgentRun)
                .where(
                    AgentRun.tenant_id == tenant,
                    AgentRun.project_id.in_(project_ids),
                    AgentRun.status.in_(("running", "queued", "paused")),
                )
                .order_by(desc(AgentRun.created_at))
            ).all()
        )
        for ar in ar_rows:
            if ar.project_id not in active_by_project:
                active_by_project[ar.project_id] = ar

    data: list[dict[str, Any]] = []
    for p in rows:
        po = ProjectOut.model_validate(p)
        active = active_by_project.get(p.id)
        if active:
            po = po.model_copy(
                update={"active_agent_run_id": active.id, "active_agent_run_status": active.status}
            )
        data.append(po.model_dump(mode="json"))
    return {
        "data": {
            "projects": data,
            "total_count": total,
            "offset": off,
            "limit": n,
        },
        "meta": meta,
    }


@router.get("/{project_id}/jobs/active")
def list_active_project_jobs(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Queued or running jobs for this project (Studio can resume polling after a browser refresh)."""
    if get_project_for_tenant(db, project_id, settings.default_tenant_id) is None:
        # Same shape as success; avoids Studio "Not Found" when project row is missing or tenant mismatches.
        return {"data": {"jobs": [], "count": 0}, "meta": meta}
    rows = list(
        db.scalars(
            select(Job)
            .where(
                and_(
                    Job.project_id == project_id,
                    Job.tenant_id == settings.default_tenant_id,
                    Job.status.in_(("queued", "running")),
                )
            )
            .order_by(desc(Job.created_at))
            .limit(100)
        ).all()
    )
    data = [JobOut.model_validate(j).model_dump(mode="json") for j in rows]
    return {"data": {"jobs": data, "count": len(data)}, "meta": meta}


@router.get("/{project_id}/agent-runs")
def list_project_agent_runs(
    project_id: UUID,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Hands-off Studio: list autonomous runs for a project (newest first)."""
    if get_project_for_tenant(db, project_id, settings.default_tenant_id) is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    n = max(1, min(int(limit), 200))
    off = max(0, int(offset))
    tid = settings.default_tenant_id
    total = int(
        db.scalar(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.project_id == project_id,
                AgentRun.tenant_id == tid,
            )
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(AgentRun)
            .where(AgentRun.project_id == project_id, AgentRun.tenant_id == tid)
            .order_by(desc(AgentRun.created_at))
            .offset(off)
            .limit(n)
        ).all()
    )
    data = [AgentRunListItem.model_validate(r).model_dump(mode="json") for r in rows]
    return {
        "data": {
            "agent_runs": data,
            "total_count": total,
            "offset": off,
            "limit": n,
        },
        "meta": meta,
    }


@router.get("/{project_id}/phase5-readiness")
def get_phase5_readiness(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    timeline_version_id: UUID | None = Query(default=None),
    export_stage: Literal["rough_cut", "fine_cut", "final_cut"] | None = Query(default=None),
    allow_unapproved_media: bool = Query(default=False),
    require_scene_narration_tracks: bool = Query(
        default=False,
        description="If true, treat missing scene TTS (for scenes with narration_text) as not ready.",
    ),
) -> dict:
    if export_stage is not None and timeline_version_id is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "BAD_REQUEST",
                "message": "timeline_version_id is required when export_stage is set",
                "phase5_gate": build_phase5_gate_payload(
                    {
                        "ready": False,
                        "primary_metric": "bad_request",
                        "issues": [
                            {
                                "code": "timeline_version_required",
                                "detail": {"export_stage": export_stage},
                            }
                        ],
                    },
                    label="BAD_REQUEST",
                ),
            },
        )
    tenant_id = str(settings.default_tenant_id)
    require_project_for_tenant(db, project_id, tenant_id)
    r = compute_phase5_readiness(
        db,
        project_id=project_id,
        tenant_id=tenant_id,
        storage_root=settings.local_storage_root,
        timeline_version_id=timeline_version_id,
        export_stage=export_stage,
        allow_unapproved_media=allow_unapproved_media,
        require_scene_narration_tracks=require_scene_narration_tracks,
    )
    if r.get("error") == "project_not_found":
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    out = dict(r)
    if not r.get("ready"):
        out["phase5_gate"] = build_phase5_gate_payload(r, label="PHASE5_NOT_READY")
    return {"data": out, "meta": meta}


@router.post("/{project_id}/assets/approve-all-succeeded")
def approve_all_succeeded_project_assets(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Approve every succeeded image/video in the project that is not yet approved (export preflight helper)."""
    require_project_for_tenant(db, project_id, settings.default_tenant_id)
    now = datetime.now(timezone.utc)
    rows = list(
        db.scalars(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.tenant_id == settings.default_tenant_id,
                Asset.status == "succeeded",
                Asset.asset_type.in_(("image", "video")),
                Asset.approved_at.is_(None),
            )
        ).all()
    )
    for a in rows:
        a.approved_at = now
        pj = dict(a.params_json) if isinstance(a.params_json, dict) else {}
        pj.pop("rejection", None)
        a.params_json = pj
    db.commit()
    log.info("project_assets_bulk_approved", project_id=str(project_id), count=len(rows))
    return {
        "data": {"approved_count": len(rows), "project_id": str(project_id)},
        "meta": meta,
    }


@router.post("/{project_id}/timeline-versions/{timeline_version_id}/reject-and-regenerate-rough-cut-images")
def reject_and_regenerate_rough_cut_timeline_images(
    project_id: UUID,
    timeline_version_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    allow_unapproved_media: bool = Query(default=False),
) -> dict:
    """Reject flagged timeline image assets (rough-cut preflight) and queue one ``scene_generate_image`` per affected scene."""
    project = require_project_for_tenant(db, project_id, settings.default_tenant_id)
    tv = _timeline_version_or_404(db, settings, timeline_version_id)
    if tv.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found for this project"},
        )
    root = Path(settings.local_storage_root).resolve()
    candidates = timeline_image_repair_svc.collect_repair_candidates(
        db,
        project=project,
        tv=tv,
        storage_root=root,
        allow_unapproved_media=allow_unapproved_media,
    )
    if not candidates:
        return {
            "data": {
                "rejected_asset_ids": [],
                "scene_ids_queued": [],
                "jobs": [],
                "note": "No flagged timeline images matched rough-cut repair rules (image + scene + not_in_project excluded).",
            },
            "meta": meta,
        }

    rejected_ids: list[str] = []
    done_assets: set[UUID] = set()
    for row in candidates:
        try:
            aid = UUID(str(row.get("asset_id")))
        except (TypeError, ValueError):
            continue
        if aid in done_assets:
            continue
        a = get_timeline_asset_for_project(db, aid, project.id)
        if a is None or str(a.asset_type or "").lower() != "image":
            continue
        timeline_image_repair_svc.reject_asset_for_repair(db, a)
        done_assets.add(aid)
        rejected_ids.append(str(aid))

    scene_order: list[UUID] = []
    seen_scenes: set[UUID] = set()
    for row in candidates:
        try:
            sid = UUID(str(row.get("scene_id")))
        except (TypeError, ValueError):
            continue
        if sid not in seen_scenes:
            seen_scenes.add(sid)
            scene_order.append(sid)

    jobs_out: list[dict[str, str]] = []
    for sid in scene_order:
        assert_can_enqueue(db, settings, "scene_generate_image", tenant_id=project.tenant_id)
        job = Job(
            id=uuid.uuid4(),
            tenant_id=settings.default_tenant_id,
            type="scene_generate_image",
            status="queued",
            payload={
                "scene_id": str(sid),
                "tenant_id": settings.default_tenant_id,
                "generation_tier": "preview",
            },
            project_id=project_id,
        )
        db.add(job)
        db.flush()
        enqueue_run_phase3_job(job.id)
        jobs_out.append({"id": str(job.id), "scene_id": str(sid)})

    db.commit()
    return {
        "data": {
            "rejected_asset_ids": rejected_ids,
            "scene_ids_queued": [str(s) for s in scene_order],
            "jobs": jobs_out,
            "note": "When image jobs finish, call reconcile-clip-images (or run it again) to point timeline clips at new stills.",
        },
        "meta": meta,
    }


@router.post("/{project_id}/timeline-versions/{timeline_version_id}/reconcile-clip-images")
def reconcile_timeline_clip_images(
    project_id: UUID,
    timeline_version_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    allow_unapproved_media: bool = Query(default=False),
) -> dict:
    """Rewrite timeline clips to use viable succeeded scene **images or videos** when the current ref is rejected, in-flight, missing, or disallowed."""
    project = require_project_for_tenant(db, project_id, settings.default_tenant_id)
    tv = _timeline_version_or_404(db, settings, timeline_version_id)
    if tv.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "timeline version not found for this project"},
        )
    root = Path(settings.local_storage_root).resolve()
    repair = timeline_image_repair_svc.run_timeline_clip_reconcile_pipeline(
        db,
        project=project,
        tv=tv,
        storage_root=root,
        allow_unapproved_media=allow_unapproved_media,
    )
    if (
        repair["relinked_assets"]
        or repair["approved_scene_stills"]
        or repair["storyboard_synced_clips"]
        or repair["rebound_clips"]
        or repair["updated_clips"]
        or repair["approved_timeline_clip_assets"]
    ):
        if repair["updated_clips"] or repair["rebound_clips"] or repair["storyboard_synced_clips"]:
            flag_modified(tv, "timeline_json")
        db.commit()
        db.refresh(tv)
    return {
        "data": {
            "relinked_assets": repair["relinked_assets"],
            "approved_scene_stills": repair["approved_scene_stills"],
            "storyboard_synced_clips": repair["storyboard_synced_clips"],
            "rebound_clips": repair["rebound_clips"],
            "updated_clips": repair["updated_clips"],
            "unchanged_clips": repair["unchanged_clips"],
            "approved_timeline_clip_assets": repair["approved_timeline_clip_assets"],
            "timeline_version_id": str(timeline_version_id),
        },
        "meta": meta,
    }


@router.get("/{project_id}/critic-reports")
def list_project_critic_reports(
    project_id: UUID,
    limit: int = 50,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Latest critic reports for a project (includes story-vs-research `target_type=project`), newest first."""
    require_project_for_tenant(db, project_id, settings.default_tenant_id)
    n = max(1, min(int(limit), 100))
    rows = list(
        db.scalars(
            select(CriticReport)
            .where(
                CriticReport.project_id == project_id,
                CriticReport.tenant_id == settings.default_tenant_id,
            )
            .order_by(desc(CriticReport.created_at))
            .limit(n)
        ).all()
    )
    data = [CriticReportOut.model_validate(r).model_dump(mode="json") for r in rows]
    return {"data": {"reports": data}, "meta": meta}


@router.get("/{project_id}/audit-events")
def list_project_audit_events(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    require_project_for_tenant(db, project_id, settings.default_tenant_id)
    ch_ids = [c[0] for c in db.execute(select(Chapter.id).where(Chapter.project_id == project_id)).all()]
    sc_ids = [s[0] for s in db.execute(select(Scene.id).join(Chapter).where(Chapter.project_id == project_id)).all()]
    scope_ids = {project_id, *ch_ids, *sc_ids}
    rows = list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == settings.default_tenant_id,
                AuditEvent.resource_id.in_(scope_ids),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(200)
        ).all()
    )
    data = [
        {
            "id": str(a.id),
            "action": a.action,
            "resource_type": a.resource_type,
            "resource_id": str(a.resource_id) if a.resource_id else None,
            "actor_id": a.actor_id,
            "payload_summary": a.payload_summary,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in rows
    ]
    return {"data": {"events": data}, "meta": meta}


@router.get("/{project_id}")
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    p = require_project_for_tenant(db, project_id, settings.default_tenant_id)
    return {"data": ProjectOut.model_validate(p).model_dump(mode="json"), "meta": meta}


@router.patch("/{project_id}")
def patch_project(
    project_id: UUID,
    body: ProjectPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    p = require_project_for_tenant(db, project_id, settings.default_tenant_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        return {"data": ProjectOut.model_validate(p).model_dump(mode="json"), "meta": meta}

    merged_brief = _brief_dict_from_project(p)
    for k in _BRIEF_FIELDS:
        if k in data and data[k] is not None:
            merged_brief[k] = data[k]
    if any(k in data for k in _BRIEF_FIELDS):
        try:
            validate_documentary_brief(merged_brief)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail={"code": "VALIDATION_ERROR", "message": str(e)},
            ) from e

    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return {"data": ProjectOut.model_validate(p).model_dump(mode="json"), "meta": meta}


@router.delete("/{project_id}")
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    p = require_project_for_tenant(db, project_id, settings.default_tenant_id)
    try:
        job_ids = [
            j[0]
            for j in db.execute(
                select(Job.id).where(Job.project_id == project_id, Job.tenant_id == settings.default_tenant_id)
            ).all()
        ]
        if job_ids:
            db.execute(delete(GenerationArtifact).where(GenerationArtifact.job_id.in_(job_ids)))
        db.execute(delete(GenerationArtifact).where(GenerationArtifact.project_id == project_id))
        db.execute(delete(Job).where(Job.project_id == project_id, Job.tenant_id == settings.default_tenant_id))
        db.delete(p)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECT_DELETE_BLOCKED",
                "message": "Project cannot be deleted because related jobs still reference it.",
            },
        )
    remove_generated_project_files(settings.local_storage_root, project_id)
    return {"data": {"deleted": True, "project_id": str(project_id)}, "meta": meta}
