"""Per-user / workspace LLM system prompts (editable defaults)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.schemas.prompts import LlmPromptItemOut, LlmPromptPatchBody
from director_api.auth.deps import auth_context_dep
from director_api.auth.context import AuthContext
from director_api.config import Settings
from director_api.db.session import get_db
from director_api.llm_prompt_catalog import all_prompt_keys
from director_api.services.llm_prompt_service import (
    delete_user_prompt_override,
    list_prompt_rows_for_api,
    upsert_user_prompt_override,
)

router = APIRouter(prefix="/prompts", tags=["prompts"])
log = structlog.get_logger(__name__)


@router.get("")
def list_llm_prompts(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    rows = list_prompt_rows_for_api(db, settings.default_tenant_id, auth.user_id)
    db.commit()
    return {
        "data": {"prompts": [LlmPromptItemOut.model_validate(r).model_dump(mode="json") for r in rows]},
        "meta": meta,
    }


@router.put("/{prompt_key}")
def put_llm_prompt(
    prompt_key: str,
    body: LlmPromptPatchBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    if prompt_key not in all_prompt_keys():
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "unknown prompt key"})
    upsert_user_prompt_override(
        db, settings.default_tenant_id, auth.user_id, prompt_key, body.content.strip()
    )
    db.commit()
    rows = list_prompt_rows_for_api(db, settings.default_tenant_id, auth.user_id)
    match = next((r for r in rows if r["prompt_key"] == prompt_key), None)
    log.info("llm_prompt_saved", prompt_key=prompt_key, tenant_id=settings.default_tenant_id)
    return {
        "data": {"prompt": LlmPromptItemOut.model_validate(match).model_dump(mode="json") if match else None},
        "meta": meta,
    }


@router.delete("/{prompt_key}/override")
def delete_llm_prompt_override_route(
    prompt_key: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    if prompt_key not in all_prompt_keys():
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "unknown prompt key"})
    deleted = delete_user_prompt_override(db, settings.default_tenant_id, auth.user_id, prompt_key)
    db.commit()
    rows = list_prompt_rows_for_api(db, settings.default_tenant_id, auth.user_id)
    match = next((r for r in rows if r["prompt_key"] == prompt_key), None)
    return {
        "data": {
            "deleted": deleted,
            "prompt": LlmPromptItemOut.model_validate(match).model_dump(mode="json") if match else None,
        },
        "meta": meta,
    }
