"""Project pipeline status (studio progress)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from director_api.api.deps import meta_dep, settings_dep
from director_api.config import Settings
from director_api.db.models import Project
from director_api.db.session import get_db
from director_api.services.pipeline_status import compute_pipeline_status
from sqlalchemy.orm import Session

router = APIRouter(tags=["pipeline"])


@router.get("/projects/{project_id}/pipeline-status")
def get_pipeline_status(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    p = db.get(Project, project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    data = compute_pipeline_status(
        db,
        project_id=project_id,
        tenant_id=settings.default_tenant_id,
        storage_root=settings.local_storage_root,
    )
    if not data.get("ok"):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": data.get("error", "unknown")})
    return {"data": data, "meta": meta}
