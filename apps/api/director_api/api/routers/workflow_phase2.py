"""Phase 2 — start, research, script routes (§14)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.deps import meta_dep, settings_dep
from director_api.auth.deps import auth_context_dep
from director_api.auth.context import AuthContext
from director_api.api.idempotency import (
    body_hash,
    idempotency_replay_or_conflict,
    require_idempotency_key,
    store_idempotency,
)
from director_api.agents import phase2_llm
from director_api.agents.openai_client import openai_compatible_configured
from director_api.services.audit import record_audit
from director_api.services.job_quota import assert_can_enqueue
from director_api.services.usage_accounting import persist_llm_usage_entries
from director_api.api.schemas.phase2 import (
    ChapterOut,
    ChapterPatch,
    ChapterScriptPatch,
    ChapterScriptRegenerateBody,
    ResearchApproveBody,
    ResearchDossierBodyPatch,
    ResearchOverrideBody,
)
from director_api.api.schemas.project import ProjectOut
from director_api.config import Settings
from director_api.db.models import Chapter, Job, Project, ResearchClaim, ResearchDossier, ResearchSource
from director_api.db.session import get_db
from director_api.services import phase2 as phase2_svc
from director_api.services.llm_prompt_runtime import llm_prompt_map_scope
from director_api.services.llm_prompt_service import build_resolved_prompt_map
from director_api.tasks.job_enqueue import enqueue_job_task
from director_api.tasks.worker_tasks import run_phase2_job
from director_api.validation.phase2_schemas import validate_director_pack, validate_research_dossier_body

router = APIRouter(tags=["phase2"])
log = structlog.get_logger(__name__)


def _project_or_404(db: Session, settings: Settings, project_id: UUID) -> Project:
    p = db.get(Project, project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    return p


def _latest_dossier(db: Session, project_id: UUID) -> ResearchDossier | None:
    return db.scalars(
        select(ResearchDossier)
        .where(ResearchDossier.project_id == project_id)
        .order_by(ResearchDossier.version.desc())
        .limit(1)
    ).first()


def _script_gate_open(d: ResearchDossier | None) -> bool:
    if not d:
        return False
    if d.status == "approved":
        return True
    return d.override_at is not None


def _assert_script_gate(db: Session, project_id: UUID) -> None:
    if not _script_gate_open(_latest_dossier(db, project_id)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RESEARCH_GATE",
                "message": "approve research or record an override before script generation",
            },
        )


def _pacing_warning(script_text: str | None, target_sec: int | None) -> str | None:
    if not script_text or not target_sec or target_sec < 30:
        return None
    wpm = 130.0
    expected = (target_sec / 60.0) * wpm
    if expected < 1:
        return None
    words = len(script_text.split())
    if words < expected * 0.58:
        return "likely_too_short_for_target_runtime"
    if words > expected * 1.48:
        return "likely_too_long_for_target_runtime"
    return None


@router.post("/projects/{project_id}/start")
def project_start(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    p = _project_or_404(db, settings, project_id)
    if p.workflow_phase != "draft":
        db.refresh(p)
        return {"data": ProjectOut.model_validate(p).model_dump(mode="json"), "meta": meta}
    if p.director_output_json is not None:
        try:
            validate_director_pack(p.director_output_json)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail={"code": "VALIDATION_ERROR", "message": str(e)},
            ) from e
        p.workflow_phase = "director_ready"
        db.commit()
        db.refresh(p)
        return {"data": ProjectOut.model_validate(p).model_dump(mode="json"), "meta": meta}
    pack = phase2_svc.build_director_pack_from_project(p)
    llm_u: list = []
    if openai_compatible_configured(settings):
        pmap = build_resolved_prompt_map(db, settings.default_tenant_id, auth.user_id)
        with llm_prompt_map_scope(pmap):
            pack = phase2_llm.enrich_director_pack(
                pack,
                p.title,
                p.topic,
                settings,
                usage_sink=llm_u,
                frame_aspect_ratio=str(getattr(p, "frame_aspect_ratio", None) or "16:9"),
            )
    if llm_u:
        persist_llm_usage_entries(
            db,
            tenant_id=settings.default_tenant_id,
            project_id=p.id,
            scene_id=None,
            asset_id=None,
            entries=llm_u,
        )
    try:
        validate_director_pack(pack)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(e)},
        ) from e
    p.director_output_json = pack
    p.workflow_phase = "director_ready"
    db.commit()
    db.refresh(p)
    log.info("project_started", project_id=str(project_id))
    return {"data": ProjectOut.model_validate(p).model_dump(mode="json"), "meta": meta}


@router.post("/projects/{project_id}/research/run")
def research_run(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    p = _project_or_404(db, settings, project_id)
    if not p.director_output_json:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_REQUIRED", "message": "POST /projects/{id}/start before research"},
        )
    if p.workflow_phase not in (
        "director_ready",
        "research_ready",
        "research_approved",
        "outline_ready",
        "chapters_ready",
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_STATE", "message": f"cannot run research in phase {p.workflow_phase}"},
        )
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/research/run"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "research_run")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="research_run",
        status="queued",
        payload={"project_id": str(project_id)},
        project_id=project_id,
    )
    db.add(job)
    p.workflow_phase = "research_running"
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase2_job, job.id)

    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.get("/projects/{project_id}/research")
def research_get(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    d = _latest_dossier(db, project_id)
    if not d:
        return {
            "data": {
                "dossier": None,
                "sources": [],
                "claims": [],
                "script_gate_open": False,
            },
            "meta": meta,
        }
    sources = db.scalars(select(ResearchSource).where(ResearchSource.dossier_id == d.id)).all()
    claims = db.scalars(select(ResearchClaim).where(ResearchClaim.dossier_id == d.id)).all()
    gate = _script_gate_open(d)
    return {
        "data": {
            "dossier": {
                "id": str(d.id),
                "version": d.version,
                "status": d.status,
                "body": d.body_json,
                "approved_at": d.approved_at.isoformat() if d.approved_at else None,
                "approved_notes": d.approved_notes,
                "override_at": d.override_at.isoformat() if d.override_at else None,
                "override_actor_id": d.override_actor_id,
                "override_reason": d.override_reason,
                "override_ticket_url": d.override_ticket_url,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            },
            "sources": [
                {
                    "id": str(s.id),
                    "url_or_reference": s.url_or_reference,
                    "title": s.title,
                    "source_type": s.source_type,
                    "credibility_score": s.credibility_score,
                    "disputed": s.disputed,
                    "notes": s.notes,
                }
                for s in sources
            ],
            "claims": [
                {
                    "id": str(c.id),
                    "claim_text": c.claim_text,
                    "confidence": c.confidence,
                    "disputed": c.disputed,
                    "adequately_sourced": c.adequately_sourced,
                    "source_refs_json": c.source_refs_json,
                }
                for c in claims
            ],
            "script_gate_open": gate,
        },
        "meta": meta,
    }


@router.patch("/projects/{project_id}/research/body")
def research_patch_body(
    project_id: UUID,
    body: ResearchDossierBodyPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Replace dossier ``body_json`` after JSON-schema validation (manual edits)."""
    _project_or_404(db, settings, project_id)
    d = _latest_dossier(db, project_id)
    if not d:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "no research dossier"})
    try:
        validate_research_dossier_body(body.body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(e)},
        ) from e
    d.body_json = body.body
    flag_modified(d, "body_json")
    db.commit()
    db.refresh(d)
    log.info("research_dossier_body_patched", project_id=str(project_id), dossier_id=str(d.id))
    return research_get(project_id, db, settings, meta)


@router.post("/projects/{project_id}/research/approve")
def research_approve(
    project_id: UUID,
    body: ResearchApproveBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    d = _latest_dossier(db, project_id)
    if not d:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "no research dossier"})
    if d.status == "approved":
        return {"data": {"dossier_id": str(d.id), "status": d.status}, "meta": meta}
    d.status = "approved"
    d.approved_at = datetime.now(timezone.utc)
    d.approved_notes = body.notes
    p = db.get(Project, project_id)
    if p:
        p.workflow_phase = "research_approved"
    db.commit()
    return {"data": {"dossier_id": str(d.id), "status": d.status}, "meta": meta}


@router.post("/projects/{project_id}/research/override")
def research_override(
    project_id: UUID,
    body: ResearchOverrideBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    d = _latest_dossier(db, project_id)
    if not d:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "no research dossier"})
    d.override_at = datetime.now(timezone.utc)
    d.override_actor_id = body.actor_user_id
    d.override_reason = body.reason
    d.override_ticket_url = body.ticket_url
    p = db.get(Project, project_id)
    if p:
        p.workflow_phase = "research_approved"
    record_audit(
        db,
        settings,
        action="research.override",
        resource_type="project",
        resource_id=project_id,
        actor_id=body.actor_user_id,
        payload_summary=body.reason[:500],
    )
    db.commit()
    log.warning(
        "research_override",
        project_id=str(project_id),
        actor=body.actor_user_id,
    )
    return {"data": {"dossier_id": str(d.id), "overridden": True}, "meta": meta}


@router.post("/projects/{project_id}/script/generate-outline")
def script_generate_outline(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _project_or_404(db, settings, project_id)
    _assert_script_gate(db, project_id)
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/script/generate-outline"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "script_outline")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="script_outline",
        status="queued",
        payload={"project_id": str(project_id)},
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase2_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.post("/projects/{project_id}/script/generate-chapters")
def script_generate_chapters(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _project_or_404(db, settings, project_id)
    _assert_script_gate(db, project_id)
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/script/generate-chapters"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "script_chapters")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="script_chapters",
        status="queued",
        payload={"project_id": str(project_id)},
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase2_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)


@router.get("/projects/{project_id}/chapters")
def list_project_chapters(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    rows = db.scalars(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)
    ).all()
    chapters = [
        ChapterOut.model_validate(ch)
        .model_copy(update={"pacing_warning": _pacing_warning(ch.script_text, ch.target_duration_sec)})
        .model_dump(mode="json")
        for ch in rows
    ]
    return {"data": {"chapters": chapters}, "meta": meta}


@router.patch("/chapters/{chapter_id}")
def patch_chapter(
    chapter_id: UUID,
    body: ChapterPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    ch = db.get(Chapter, chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    p = db.get(Project, ch.project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    if body.title is not None:
        ch.title = body.title
    if body.summary is not None:
        ch.summary = body.summary
    if body.target_duration_sec is not None:
        ch.target_duration_sec = body.target_duration_sec
    if body.script_text is not None:
        ch.script_text = body.script_text
    db.commit()
    db.refresh(ch)
    out = (
        ChapterOut.model_validate(ch)
        .model_copy(update={"pacing_warning": _pacing_warning(ch.script_text, ch.target_duration_sec)})
        .model_dump(mode="json")
    )
    return {"data": out, "meta": meta}


@router.patch("/chapters/{chapter_id}/script")
def patch_chapter_script(
    chapter_id: UUID,
    body: ChapterScriptPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    ch = db.get(Chapter, chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    p = db.get(Project, ch.project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    ch.script_text = body.script_text
    db.commit()
    db.refresh(ch)
    return {
        "data": {
            "id": str(ch.id),
            "project_id": str(ch.project_id),
            "order_index": ch.order_index,
            "title": ch.title,
            "script_text": ch.script_text,
            "target_duration_sec": ch.target_duration_sec,
        },
        "meta": meta,
    }


@router.post("/chapters/{chapter_id}/script/regenerate")
def chapter_script_regenerate(
    chapter_id: UUID,
    body: ChapterScriptRegenerateBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Queue LLM job to rewrite one chapter's script using ``enhancement_notes`` (e.g. chapter summary / edit notes)."""
    ch = db.get(Chapter, chapter_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    p = db.get(Project, ch.project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "chapter not found"})
    _assert_script_gate(db, p.id)
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/chapters/{chapter_id}/script/regenerate"
    h = body_hash(body.model_dump())
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "script_chapter_regenerate")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="script_chapter_regenerate",
        status="queued",
        payload={
            "project_id": str(p.id),
            "chapter_id": str(ch.id),
            "enhancement_notes": body.enhancement_notes.strip(),
        },
        project_id=p.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase2_job, job.id)
    response_body = {
        "job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"},
        "meta": meta,
    }
    store_idempotency(
        db,
        tenant_id=settings.default_tenant_id,
        route=route,
        key=key,
        h=h,
        response_status=202,
        response_body=response_body,
    )
    return JSONResponse(status_code=202, content=response_body)
