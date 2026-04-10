"""Chat Studio — project setup guide (LLM)."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.schemas.chat_studio import ChatStudioGuideRequest
from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.config import Settings, get_settings
from director_api.db.models import Project
from director_api.db.session import get_db
from director_api.services.chat_studio_guide import run_setup_guide_turn
from director_api.services.tenant_entitlements import assert_chat_allowed

router = APIRouter(prefix="/chat-studio", tags=["chat-studio"])
log = structlog.get_logger(__name__)


def _brief_snapshot_from_project(p: Project) -> dict[str, Any]:
    return {
        "title": p.title,
        "topic": p.topic,
        "target_runtime_minutes": p.target_runtime_minutes,
        "audience": p.audience,
        "tone": p.tone,
        "visual_style": p.visual_style,
        "narration_style": p.narration_style,
        "factual_strictness": p.factual_strictness,
        "music_preference": p.music_preference,
        "research_min_sources": p.research_min_sources,
        "preferred_text_provider": p.preferred_text_provider,
        "preferred_image_provider": p.preferred_image_provider,
        "preferred_video_provider": p.preferred_video_provider,
        "preferred_speech_provider": p.preferred_speech_provider,
    }


@router.post("/setup-guide")
def post_setup_guide(
    body: ChatStudioGuideRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    _: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    auth_on = bool(get_settings().director_auth_enabled)
    assert_chat_allowed(db=db, tenant_id=settings.default_tenant_id, auth_enabled=auth_on)

    snap: dict[str, Any] = {}
    if isinstance(body.current_brief, dict):
        snap.update(body.current_brief)

    if body.project_id is not None:
        p = db.get(Project, body.project_id)
        if not p or p.tenant_id != settings.default_tenant_id:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
        server_snap = _brief_snapshot_from_project(p)
        server_snap.update(snap)
        snap = server_snap

    raw_messages = [m.model_dump() for m in body.messages]
    data, err = run_setup_guide_turn(settings, messages=raw_messages, brief_snapshot=snap)
    if err:
        log.warning("chat_studio_setup_guide_failed", error=err)
        raise HTTPException(
            status_code=502,
            detail={"code": "SETUP_GUIDE_LLM_FAILED", "message": err},
        )

    return {"data": data, "meta": meta}
