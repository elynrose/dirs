"""Publish pack routes — thumbnail, opening hook, optional outro."""

from __future__ import annotations

import uuid
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.idempotency import (
    body_hash,
    idempotency_replay_or_conflict,
    require_idempotency_key,
    store_idempotency,
)
from director_api.api.schemas.project import ProjectOut
from director_api.api.tenant_access import require_project_for_tenant
from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.config import Settings
from director_api.db.models import Job, Project
from director_api.db.session import get_db
from director_api.services.job_quota import assert_can_enqueue
from director_api.services.publish_hook import find_hook_scene, remove_hook_scene, sync_hook_scene_from_project
from director_api.services.publish_outro import find_outro_scene, remove_outro_scene
from director_api.services.publish_pack import (
    merge_publish_pack,
    resolve_thumbnail_content_path,
    save_thumbnail_upload,
)
from director_api.services.research_service import sanitize_jsonb_text
from director_api.tasks.job_enqueue import enqueue_job_task
from director_api.tasks.worker_tasks import run_phase2_job

router = APIRouter(prefix="/projects", tags=["publish"])
log = structlog.get_logger(__name__)

_THUMB_MAX_BYTES = 8 * 1024 * 1024


class PublishPackPatch(BaseModel):
    youtube_title: str | None = Field(default=None, max_length=100)
    youtube_description: str | None = Field(default=None, max_length=5000)


class OpeningHookPatch(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


class IncludeOutroPatch(BaseModel):
    include_outro_scene: bool


class PublishToYoutubePatch(BaseModel):
    publish_to_youtube: bool


@router.post("/{project_id}/thumbnail/generate")
def thumbnail_generate(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    require_project_for_tenant(db, project_id, auth.tenant_id)
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/thumbnail/generate"
    h = body_hash({})
    replay = idempotency_replay_or_conflict(db, tenant_id=auth.tenant_id, route=route, key=key, h=h)
    if replay:
        return replay
    assert_can_enqueue(db, settings, "thumbnail_generate", tenant_id=auth.tenant_id)
    job = Job(
        id=uuid.uuid4(),
        tenant_id=auth.tenant_id,
        type="thumbnail_generate",
        status="queued",
        payload={"tenant_id": auth.tenant_id, "project_id": str(project_id)},
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase2_job, job.id)
    body = {"job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"}, "meta": meta}
    store_idempotency(
        db, tenant_id=auth.tenant_id, route=route, key=key, h=h, response_status=202, response_body=body
    )
    return JSONResponse(status_code=202, content=body)


@router.post("/{project_id}/thumbnail/upload")
async def thumbnail_upload(
    project_id: UUID,
    file: UploadFile = File(...),
    youtube_title: str | None = Form(default=None),
    youtube_description: str | None = Form(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    project = require_project_for_tenant(db, project_id, auth.tenant_id)
    raw_parts: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _THUMB_MAX_BYTES:
            raise HTTPException(status_code=413, detail={"code": "TOO_LARGE", "message": "thumbnail exceeds 8 MB"})
        raw_parts.append(chunk)
    raw = b"".join(raw_parts)
    ct = file.content_type or "image/png"
    pack = save_thumbnail_upload(
        db,
        project,
        settings,
        raw=raw,
        content_type=ct,
        youtube_title=youtube_title,
        youtube_description=youtube_description,
    )
    db.commit()
    db.refresh(project)
    return {"data": {"publish_pack": pack, "project": ProjectOut.model_validate(project).model_dump(mode="json")}, "meta": meta}


@router.get("/{project_id}/thumbnail/content")
def thumbnail_content(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
):
    project = require_project_for_tenant(db, project_id, auth.tenant_id)
    path = resolve_thumbnail_content_path(project, settings)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "no thumbnail image"})
    return FileResponse(path=path, media_type="image/png", filename="thumbnail.png")


@router.patch("/{project_id}/publish-pack")
def patch_publish_pack(
    project_id: UUID,
    body: PublishPackPatch,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    project = require_project_for_tenant(db, project_id, auth.tenant_id)
    patch: dict = {}
    if body.youtube_title is not None:
        patch["youtube_title"] = sanitize_jsonb_text(body.youtube_title.strip(), 100)
    if body.youtube_description is not None:
        patch["youtube_description"] = sanitize_jsonb_text(body.youtube_description.strip(), 5000)
    if not patch:
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "no fields to update"})
    project.publish_pack_json = merge_publish_pack(project, patch)
    flag_modified(project, "publish_pack_json")
    db.commit()
    db.refresh(project)
    return {"data": {"publish_pack": project.publish_pack_json}, "meta": meta}


@router.post("/{project_id}/opening-hook/generate")
def opening_hook_generate(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    require_project_for_tenant(db, project_id, auth.tenant_id)
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/opening-hook/generate"
    h = body_hash({})
    replay = idempotency_replay_or_conflict(db, tenant_id=auth.tenant_id, route=route, key=key, h=h)
    if replay:
        return replay
    assert_can_enqueue(db, settings, "opening_hook_generate", tenant_id=auth.tenant_id)
    job = Job(
        id=uuid.uuid4(),
        tenant_id=auth.tenant_id,
        type="opening_hook_generate",
        status="queued",
        payload={"tenant_id": auth.tenant_id, "project_id": str(project_id)},
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase2_job, job.id)
    body = {"job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"}, "meta": meta}
    store_idempotency(
        db, tenant_id=auth.tenant_id, route=route, key=key, h=h, response_status=202, response_body=body
    )
    return JSONResponse(status_code=202, content=body)


@router.patch("/{project_id}/opening-hook")
def patch_opening_hook(
    project_id: UUID,
    body: OpeningHookPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    project = require_project_for_tenant(db, project_id, auth.tenant_id)
    project.opening_hook_text = sanitize_jsonb_text(body.text.strip(), 8000)
    if project.workflow_phase in ("chapters_ready", "thumbnail_ready"):
        project.workflow_phase = "hook_ready"
    sync_hook_scene_from_project(db, project, settings)
    db.commit()
    db.refresh(project)
    return {"data": {"opening_hook_text": project.opening_hook_text}, "meta": meta}


@router.post("/{project_id}/hook/append")
def hook_append(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    require_project_for_tenant(db, project_id, auth.tenant_id)
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/hook/append"
    h = body_hash({})
    replay = idempotency_replay_or_conflict(db, tenant_id=auth.tenant_id, route=route, key=key, h=h)
    if replay:
        return replay
    assert_can_enqueue(db, settings, "hook_scene_append", tenant_id=auth.tenant_id)
    job = Job(
        id=uuid.uuid4(),
        tenant_id=auth.tenant_id,
        type="hook_scene_append",
        status="queued",
        payload={"tenant_id": auth.tenant_id, "project_id": str(project_id)},
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase2_job, job.id)
    body = {"job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"}, "meta": meta}
    store_idempotency(
        db, tenant_id=auth.tenant_id, route=route, key=key, h=h, response_status=202, response_body=body
    )
    return JSONResponse(status_code=202, content=body)


@router.delete("/{project_id}/hook")
def hook_delete(
    project_id: UUID,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    require_project_for_tenant(db, project_id, auth.tenant_id)
    removed = remove_hook_scene(db, project_id)
    db.commit()
    return {"data": {"removed": removed}, "meta": meta}


@router.get("/{project_id}/hook")
def hook_get(
    project_id: UUID,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    require_project_for_tenant(db, project_id, auth.tenant_id)
    sc = find_hook_scene(db, project_id)
    if not sc:
        return {"data": {"hook_scene": None}, "meta": meta}
    return {
        "data": {
            "hook_scene": {
                "id": str(sc.id),
                "chapter_id": str(sc.chapter_id),
                "narration_text": sc.narration_text,
                "order_index": sc.order_index,
            }
        },
        "meta": meta,
    }


@router.patch("/{project_id}/outro-settings")
def patch_outro_settings(
    project_id: UUID,
    body: IncludeOutroPatch,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    project = require_project_for_tenant(db, project_id, auth.tenant_id)
    project.include_outro_scene = bool(body.include_outro_scene)
    if not project.include_outro_scene:
        remove_outro_scene(db, project_id)
    db.commit()
    db.refresh(project)
    return {
        "data": {"include_outro_scene": project.include_outro_scene},
        "meta": meta,
    }


@router.patch("/{project_id}/publish-settings")
def patch_publish_settings(
    project_id: UUID,
    body: PublishToYoutubePatch,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    project = require_project_for_tenant(db, project_id, auth.tenant_id)
    project.publish_to_youtube = bool(body.publish_to_youtube)
    db.commit()
    db.refresh(project)
    return {
        "data": {"publish_to_youtube": project.publish_to_youtube},
        "meta": meta,
    }


@router.post("/{project_id}/outro/append")
def outro_append(
    project_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    project = require_project_for_tenant(db, project_id, auth.tenant_id)
    if not project.include_outro_scene:
        project.include_outro_scene = True
    key = require_idempotency_key(idempotency_key)
    route = f"POST /v1/projects/{project_id}/outro/append"
    h = body_hash({})
    replay = idempotency_replay_or_conflict(db, tenant_id=auth.tenant_id, route=route, key=key, h=h)
    if replay:
        return replay
    assert_can_enqueue(db, settings, "outro_append", tenant_id=auth.tenant_id)
    job = Job(
        id=uuid.uuid4(),
        tenant_id=auth.tenant_id,
        type="outro_append",
        status="queued",
        payload={"tenant_id": auth.tenant_id, "project_id": str(project_id)},
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_job_task(run_phase2_job, job.id)
    body = {"job": {"id": str(job.id), "status": job.status, "poll_url": f"/v1/jobs/{job.id}"}, "meta": meta}
    store_idempotency(
        db, tenant_id=auth.tenant_id, route=route, key=key, h=h, response_status=202, response_body=body
    )
    return JSONResponse(status_code=202, content=body)


@router.delete("/{project_id}/outro")
def outro_delete(
    project_id: UUID,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    require_project_for_tenant(db, project_id, auth.tenant_id)
    removed = remove_outro_scene(db, project_id)
    db.commit()
    return {"data": {"removed": removed}, "meta": meta}


@router.get("/{project_id}/outro")
def outro_get(
    project_id: UUID,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    require_project_for_tenant(db, project_id, auth.tenant_id)
    sc = find_outro_scene(db, project_id)
    if not sc:
        return {"data": {"outro_scene": None}, "meta": meta}
    return {
        "data": {
            "outro_scene": {
                "id": str(sc.id),
                "chapter_id": str(sc.chapter_id),
                "narration_text": sc.narration_text,
                "order_index": sc.order_index,
            }
        },
        "meta": meta,
    }
