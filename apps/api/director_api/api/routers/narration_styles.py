"""User narration style library (voice briefs) merged with built-in presets."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.schemas.narration_styles import (
    NarrationStyleCreateBody,
    NarrationStyleItemOut,
    NarrationStylePatchBody,
    NarrationStyleRowOut,
)
from director_api.auth.deps import auth_context_dep
from director_api.auth.context import AuthContext
from director_api.config import Settings
from director_api.db.session import get_db
from director_api.services import narration_style_service as nar_svc

router = APIRouter(prefix="/narration-styles", tags=["narration-styles"])


@router.get("")
def list_narration_styles(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    uid = int(auth.user_id) if auth.user_id else None
    rows = nar_svc.list_merged_styles(db, settings.default_tenant_id, uid)
    db.commit()
    return {
        "data": {
            "styles": [NarrationStyleItemOut.model_validate(r).model_dump(mode="json") for r in rows],
        },
        "meta": meta,
    }


@router.post("")
def create_narration_style(
    body: NarrationStyleCreateBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    if not auth.user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "sign in to create styles"})
    row = nar_svc.create_style(
        db,
        settings.default_tenant_id,
        int(auth.user_id),
        body.title,
        body.prompt_text,
    )
    db.commit()
    db.refresh(row)
    return {
        "data": {"style": NarrationStyleRowOut.model_validate(row).model_dump(mode="json")},
        "meta": meta,
    }


@router.patch("/{style_id}")
def patch_narration_style(
    style_id: uuid.UUID,
    body: NarrationStylePatchBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    if not auth.user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "sign in to edit styles"})
    if body.title is None and body.prompt_text is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": "provide title and/or prompt_text"},
        )
    row = nar_svc.patch_style(
        db,
        settings.default_tenant_id,
        int(auth.user_id),
        style_id,
        title=body.title,
        prompt_text=body.prompt_text,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "style not found"})
    db.commit()
    db.refresh(row)
    return {
        "data": {"style": NarrationStyleRowOut.model_validate(row).model_dump(mode="json")},
        "meta": meta,
    }


@router.delete("/{style_id}")
def delete_narration_style(
    style_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    if not auth.user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "sign in to delete styles"})
    ok = nar_svc.delete_style(db, settings.default_tenant_id, int(auth.user_id), style_id)
    if not ok:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "style not found"})
    db.commit()
    return {"data": {"deleted": True}, "meta": meta}
