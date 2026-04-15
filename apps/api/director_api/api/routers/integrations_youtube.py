"""YouTube Data API: OAuth connect, manual upload, share links (workspace-scoped credentials)."""

from __future__ import annotations

import html
import uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.deps import meta_dep, settings_dep
from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.config import Settings, get_settings
from director_api.db.models import Project, TimelineVersion
from director_api.db.session import get_db
from director_api.services.runtime_settings import (
    get_or_create_app_settings,
    invalidate_runtime_settings_cache_after_tenant_config_persisted,
    sanitize_overrides,
)
from director_api.services.youtube_upload import (
    build_google_authorize_url,
    exchange_authorization_code,
    refresh_access_token,
    share_url_from_upload_response,
    sign_youtube_oauth_state,
    upload_mp4_resumable,
    verify_youtube_oauth_state,
)
from ffmpeg_pipelines.paths import path_is_readable_file

router = APIRouter(prefix="/integrations/youtube", tags=["integrations"])
log = structlog.get_logger(__name__)


def _oauth_redirect_uri(request: Request, base: Settings) -> str:
    pub = (getattr(base, "public_api_base_url", None) or "").strip().rstrip("/")
    if pub:
        return f"{pub}/v1/integrations/youtube/oauth-callback"
    return str(request.url_for("youtube_oauth_callback"))


def _persist_youtube_refresh(db: Session, tenant_id: str, refresh_token: str) -> None:
    row = get_or_create_app_settings(db, tenant_id)
    cfg = dict(row.config_json or {})
    cfg["youtube_refresh_token"] = refresh_token.strip()
    row.config_json = sanitize_overrides(cfg)
    db.add(row)
    db.commit()
    invalidate_runtime_settings_cache_after_tenant_config_persisted(get_settings(), tenant_id)


def _clear_youtube_refresh(db: Session, tenant_id: str) -> None:
    row = get_or_create_app_settings(db, tenant_id)
    cfg = dict(row.config_json or {})
    cfg.pop("youtube_refresh_token", None)
    row.config_json = sanitize_overrides(cfg)
    db.add(row)
    db.commit()
    invalidate_runtime_settings_cache_after_tenant_config_persisted(get_settings(), tenant_id)


class YoutubeManualUploadBody(BaseModel):
    project_id: uuid.UUID
    timeline_version_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    privacy_status: str = Field(default="unlisted")


@router.get("/status")
def youtube_status(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    _auth: AuthContext = Depends(auth_context_dep),
) -> dict[str, Any]:
    has_refresh = bool((settings.youtube_refresh_token or "").strip())
    has_client = bool((settings.youtube_client_id or "").strip() and (settings.youtube_client_secret or "").strip())
    return {
        "data": {
            "connected": has_refresh and has_client,
            "has_client_id": bool((settings.youtube_client_id or "").strip()),
            "has_client_secret": bool((settings.youtube_client_secret or "").strip()),
            "youtube_auto_upload_after_export": bool(settings.youtube_auto_upload_after_export),
            "youtube_default_privacy": (settings.youtube_default_privacy or "unlisted").strip().lower(),
        },
        "meta": meta,
    }


@router.get("/auth-url")
def youtube_auth_url(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    auth: AuthContext = Depends(auth_context_dep),
) -> dict[str, Any]:
    base = get_settings()
    secret = (base.director_jwt_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MISSING_SIGNING_SECRET",
                "message": "DIRECTOR_JWT_SECRET is required to sign YouTube OAuth state (symmetric HMAC, not user JWTs)",
            },
        )
    cid = (settings.youtube_client_id or "").strip()
    csec = (settings.youtube_client_secret or "").strip()
    if not cid or not csec:
        raise HTTPException(
            status_code=400,
            detail={"code": "YOUTUBE_CLIENT_NOT_CONFIGURED", "message": "Set youtube_client_id and youtube_client_secret"},
        )
    redirect_uri = _oauth_redirect_uri(request, base)
    state = sign_youtube_oauth_state(auth.tenant_id, secret)
    url = build_google_authorize_url(client_id=cid, redirect_uri=redirect_uri, state=state)
    return {"data": {"authorize_url": url, "redirect_uri": redirect_uri, "state": state}, "meta": meta}


@router.get("/oauth-callback", name="youtube_oauth_callback")
def youtube_oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> HTMLResponse:
    base = get_settings()
    secret = (base.director_jwt_secret or "").strip()
    if error:
        return HTMLResponse(
            f"<html><body><p>Google returned an error: {html.escape(str(error))}</p><p>You can close this tab.</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")
    if not secret:
        return HTMLResponse(
            "<html><body>Server missing DIRECTOR_JWT_SECRET (OAuth state signing). Close this tab and fix API config.</body></html>",
            status_code=503,
        )
    tenant_id = verify_youtube_oauth_state(state, secret)
    if not tenant_id:
        return HTMLResponse(
            "<html><body>Invalid or expired OAuth state. Close this tab and start Connect again from Studio.</body></html>",
            status_code=400,
        )
    cid = (base.youtube_client_id or "").strip()
    csec = (base.youtube_client_secret or "").strip()
    if not cid or not csec:
        return HTMLResponse(
            "<html><body>YouTube client id/secret not set on the server. Configure them, then try again.</body></html>",
            status_code=400,
        )
    redirect_uri = _oauth_redirect_uri(request, base)
    try:
        tok = exchange_authorization_code(
            code=code,
            client_id=cid,
            client_secret=csec,
            redirect_uri=redirect_uri,
        )
    except Exception as exc:
        log.warning("youtube_oauth_exchange_failed", error=str(exc)[:500])
        return HTMLResponse(
            f"<html><body><p>Could not exchange authorization code.</p><pre>{html.escape(str(exc)[:800])}</pre></body></html>",
            status_code=400,
        )
    rt = str(tok.get("refresh_token") or "").strip()
    if not rt:
        return HTMLResponse(
            "<html><body>No refresh_token in response. Revoke app access in Google Account, then connect again with prompt=consent.</body></html>",
            status_code=400,
        )
    _persist_youtube_refresh(db, tenant_id, rt)
    log.info("youtube_oauth_connected", tenant_id=tenant_id)
    return HTMLResponse(
        "<html><body><p>YouTube connected for this workspace.</p><p>You can close this tab and return to Directely Studio.</p></body></html>"
    )


@router.post("/disconnect")
def youtube_disconnect(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    auth: AuthContext = Depends(auth_context_dep),
) -> dict[str, Any]:
    _clear_youtube_refresh(db, auth.tenant_id)
    return {"data": {"ok": True}, "meta": meta}


@router.post("/upload")
def youtube_manual_upload(
    body: YoutubeManualUploadBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
    auth: AuthContext = Depends(auth_context_dep),
) -> dict[str, Any]:
    p = db.get(Project, body.project_id)
    if not p or p.tenant_id != settings.default_tenant_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "project not found"})
    tv = db.get(TimelineVersion, body.timeline_version_id)
    if not tv or tv.tenant_id != settings.default_tenant_id or tv.project_id != body.project_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "timeline version not found"})

    cid = (settings.youtube_client_id or "").strip()
    csec = (settings.youtube_client_secret or "").strip()
    rtok = (settings.youtube_refresh_token or "").strip()
    if not cid or not csec or not rtok:
        raise HTTPException(
            status_code=400,
            detail={"code": "YOUTUBE_NOT_CONNECTED", "message": "Connect YouTube in Settings or set refresh token"},
        )
    root = Path(settings.local_storage_root).resolve()
    vid = root / "exports" / str(body.project_id) / str(body.timeline_version_id) / "final_cut.mp4"
    if not path_is_readable_file(vid):
        raise HTTPException(
            status_code=400,
            detail={"code": "NO_FINAL_CUT", "message": "final_cut.mp4 not found — run final cut first"},
        )
    priv = (body.privacy_status or "unlisted").strip().lower()
    if priv not in ("public", "unlisted", "private"):
        priv = "unlisted"
    try:
        access = refresh_access_token(refresh_token=rtok, client_id=cid, client_secret=csec)
        resp = upload_mp4_resumable(
            access_token=access,
            file_path=vid,
            title=body.title.strip()[:100],
            description=(body.description or "")[:5000],
            privacy_status=priv,
        )
    except Exception as exc:
        log.warning("youtube_manual_upload_failed", error=str(exc)[:500])
        raise HTTPException(status_code=502, detail={"code": "YOUTUBE_UPLOAD_FAILED", "message": str(exc)[:2000]}) from exc

    url = share_url_from_upload_response(resp)
    tj = dict(tv.timeline_json or {}) if isinstance(tv.timeline_json, dict) else {}
    tj["youtube_last_upload"] = {"video_id": str(resp.get("id") or ""), "watch_url": url}
    tv.timeline_json = tj
    flag_modified(tv, "timeline_json")
    db.add(tv)
    db.commit()
    return {
        "data": {
            "video_id": str(resp.get("id") or ""),
            "watch_url": url,
            "share_url": url,
        },
        "meta": meta,
    }
