"""Project character bible — CRUD + LLM generate job."""

from __future__ import annotations

import uuid
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.idempotency import (
    body_hash,
    idempotency_replay_or_conflict,
    require_idempotency_key,
    store_idempotency,
)
from director_api.api.schemas.character import (
    ProjectCharacterCreate,
    ProjectCharacterOut,
    ProjectCharacterPatch,
)
from director_api.config import Settings
from director_api.db.models import Job, Project, ProjectCharacter
from director_api.db.session import get_db
from director_api.services.job_quota import assert_can_enqueue
from director_api.services.research_service import sanitize_jsonb_text
from director_api.tasks.job_enqueue import enqueue_job_task
from director_api.tasks.worker_tasks import run_phase2_job

router = APIRouter(prefix="/projects", tags=["characters"])
log = structlog.get_logger(__name__)


def _project_or_404(db: Session, settings: Settings, project_id: UUID) -> Project:
    p = db.get(Project, project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    return p


def _char_or_404(db: Session, settings: Settings, project_id: UUID, character_id: UUID) -> ProjectCharacter:
    c = db.get(ProjectCharacter, character_id)
    if not c or c.tenant_id != settings.default_tenant_id or c.project_id != project_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "character not found"})
    return c


@router.get("/{project_id}/characters")
def list_characters(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    _project_or_404(db, settings, project_id)
    rows = db.scalars(
        select(ProjectCharacter)
        .where(ProjectCharacter.project_id == project_id)
        .order_by(ProjectCharacter.sort_order.asc(), ProjectCharacter.name.asc())
    ).all()
    return {
        "data": {"characters": [ProjectCharacterOut.model_validate(r).model_dump(mode="json") for r in rows]},
        "meta": meta,
    }


@router.post("/{project_id}/characters/generate")
def generate_characters(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _project_or_404(db, settings, project_id)
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/characters/generate"
    empty: dict = {}
    h = body_hash(empty)
    replay = idempotency_replay_or_conflict(db, tenant_id=settings.default_tenant_id, route=route, key=key, h=h)
    if replay:
        return replay

    assert_can_enqueue(db, settings, "characters_generate")
    job = Job(
        id=uuid.uuid4(),
        tenant_id=settings.default_tenant_id,
        type="characters_generate",
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


@router.post("/{project_id}/characters")
def create_character(
    project_id: UUID,
    body: ProjectCharacterCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    p = _project_or_404(db, settings, project_id)
    max_so = db.scalar(select(func.max(ProjectCharacter.sort_order)).where(ProjectCharacter.project_id == project_id))
    next_so = int(max_so or -1) + 1
    c = ProjectCharacter(
        id=uuid.uuid4(),
        tenant_id=p.tenant_id,
        project_id=p.id,
        sort_order=next_so,
        name=sanitize_jsonb_text(body.name, 256),
        role_in_story=sanitize_jsonb_text(body.role_in_story, 2000),
        visual_description=sanitize_jsonb_text(body.visual_description, 8000),
        time_place_scope_notes=sanitize_jsonb_text(body.time_place_scope_notes, 2000) if body.time_place_scope_notes else None,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"data": ProjectCharacterOut.model_validate(c).model_dump(mode="json"), "meta": meta}


@router.patch("/{project_id}/characters/{character_id}")
def patch_character(
    project_id: UUID,
    character_id: UUID,
    body: ProjectCharacterPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    c = _char_or_404(db, settings, project_id, character_id)
    if body.sort_order is not None:
        c.sort_order = int(body.sort_order)
    if body.name is not None:
        c.name = sanitize_jsonb_text(body.name, 256)
    if body.role_in_story is not None:
        c.role_in_story = sanitize_jsonb_text(body.role_in_story, 2000)
    if body.visual_description is not None:
        c.visual_description = sanitize_jsonb_text(body.visual_description, 8000)
    if body.time_place_scope_notes is not None:
        t = body.time_place_scope_notes.strip()
        c.time_place_scope_notes = sanitize_jsonb_text(t, 2000) if t else None
    db.commit()
    db.refresh(c)
    return {"data": ProjectCharacterOut.model_validate(c).model_dump(mode="json"), "meta": meta}


@router.delete("/{project_id}/characters/{character_id}")
def delete_character(
    project_id: UUID,
    character_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    c = _char_or_404(db, settings, project_id, character_id)
    db.delete(c)
    db.commit()
    return {"data": {"deleted": True, "id": str(character_id)}, "meta": meta}
