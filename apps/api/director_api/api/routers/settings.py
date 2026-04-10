from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.routers.fal_catalog import load_fal_models_data
from director_api.api.schemas.prompts import LlmPromptItemOut, LlmPromptPatchBody
from director_api.api.schemas.settings import AppSettingsPatch, AppSettingsOut
from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.config import Settings
from director_api.db.session import get_db
from director_api.providers.speech_route import resolve_chatterbox_ref_to_path
from ffmpeg_pipelines.paths import path_is_readable_file
from director_api.style_presets import style_presets_public_payload
from director_api.services.chatterbox_voice_ref import (
    convert_upload_to_reference_wav,
    safe_tenant_slug,
    voice_ref_absolute_path,
    voice_ref_storage_key,
)
from director_api.services.runtime_settings import (
    get_or_create_app_settings,
    invalidate_runtime_settings_cache,
    resolve_runtime_settings,
    sanitize_overrides,
)
from director_api.voice_catalog import gemini_tts_voices_payload
from director_api.llm_prompt_catalog import all_prompt_keys
from director_api.services.llm_prompt_service import (
    delete_user_prompt_override,
    list_prompt_rows_for_api,
    upsert_user_prompt_override,
)
from director_api.services.usage_accounting import usage_summary_for_tenant
from director_api.config import get_settings
from director_api.services.telegram_client import telegram_get_me, telegram_send_message
from director_api.services.tenant_entitlements import assert_telegram_allowed

router = APIRouter(prefix="/settings", tags=["settings"])
log = structlog.get_logger(__name__)

_CHATTERBOX_REF_MAX_BYTES = 25 * 1024 * 1024


@router.get("")
def get_settings_row(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    row = get_or_create_app_settings(db, settings.default_tenant_id)
    db.commit()
    db.refresh(row)
    return {
        "data": AppSettingsOut(
            id=row.id,
            tenant_id=row.tenant_id,
            config=sanitize_overrides(row.config_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
        ).model_dump(mode="json"),
        "meta": meta,
    }


@router.patch("")
def patch_settings_row(
    body: AppSettingsPatch,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    row = get_or_create_app_settings(db, settings.default_tenant_id)
    prior = sanitize_overrides(row.config_json)
    patch = sanitize_overrides(body.config)
    row.config_json = {**prior, **patch}
    db.commit()
    db.refresh(row)
    invalidate_runtime_settings_cache(settings.default_tenant_id)
    return {
        "data": AppSettingsOut(
            id=row.id,
            tenant_id=row.tenant_id,
            config=sanitize_overrides(row.config_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
        ).model_dump(mode="json"),
        "meta": meta,
    }


@router.get("/style-presets")
def get_style_presets(meta: dict = Depends(meta_dep)) -> dict:
    """Ids, labels, and prompt text for narration / visual presets (UI + brief `preset:<id>`)."""
    return {"data": style_presets_public_payload(), "meta": meta}


@router.get("/gemini-tts-voices")
def get_gemini_tts_voices(meta: dict = Depends(meta_dep)) -> dict:
    """Prebuilt Gemini TTS voice names (static list from Google documentation)."""
    return {"data": gemini_tts_voices_payload(), "meta": meta}


@router.get("/elevenlabs-voices")
def get_elevenlabs_voices(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Voices available to the workspace ElevenLabs account (requires saved API key)."""
    rt = resolve_runtime_settings(db, settings)
    key = (getattr(rt, "elevenlabs_api_key", None) or "").strip()
    if not key:
        return {
            "data": {"voices": [], "error": "no_api_key"},
            "meta": {**meta, "hint": "Save ELEVENLABS_API_KEY in settings, then refresh this list."},
        }
    url = "https://api.elevenlabs.io/v1/voices"
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers={"xi-api-key": key})
    except httpx.HTTPError as e:
        return {
            "data": {"voices": [], "error": "request_failed"},
            "meta": {**meta, "detail": str(e)[:500]},
        }
    if r.status_code >= 400:
        return {
            "data": {"voices": [], "error": f"http_{r.status_code}"},
            "meta": {**meta, "detail": (r.text or "")[:500]},
        }
    try:
        raw = r.json()
    except Exception:  # noqa: BLE001
        return {"data": {"voices": [], "error": "invalid_json"}, "meta": meta}
    voices_in = raw.get("voices") if isinstance(raw, dict) else None
    if not isinstance(voices_in, list):
        return {"data": {"voices": [], "error": "unexpected_shape"}, "meta": meta}
    out: list[dict[str, Any]] = []
    for v in voices_in:
        if not isinstance(v, dict):
            continue
        vid = v.get("voice_id")
        name = v.get("name")
        if not isinstance(vid, str) or not vid.strip():
            continue
        label = f"{name} — {vid}" if isinstance(name, str) and name.strip() else vid
        row: dict[str, Any] = {"id": vid.strip(), "label": label.strip()}
        cat = v.get("category")
        if isinstance(cat, str) and cat.strip():
            row["category"] = cat.strip()
        out.append(row)
    out.sort(key=lambda x: (x.get("label") or x["id"]).lower())
    return {"data": {"voices": out}, "meta": meta}


@router.get("/usage-summary")
def get_usage_summary(
    days: int = Query(default=30, ge=1, le=366),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """LLM token totals and rough cost estimates for the workspace (same tenant as settings)."""
    tenant_id = str(settings.default_tenant_id)
    data = usage_summary_for_tenant(db, tenant_id=tenant_id, days=days)
    return {"data": data, "meta": {**meta, "tenant_id": tenant_id}}


@router.get("/fal-models")
def list_fal_models_via_settings(
    media: Literal["image", "video"] = Query(
        ...,
        description="image → text-to-image + image-to-image (merged); video → text-to-video + image-to-video (merged, active only)",
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> JSONResponse:
    """Same catalog as GET /v1/fal/models; colocated with settings for stable routing."""
    data = load_fal_models_data(db, settings, media)
    return JSONResponse(
        content={"data": data, "meta": meta},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/chatterbox-voice-ref")
def get_chatterbox_voice_ref_info(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Whether a workspace Chatterbox reference clip exists and which storage key is configured."""
    row = get_or_create_app_settings(db, settings.default_tenant_id)
    rt = resolve_runtime_settings(db, settings)
    key = (getattr(rt, "chatterbox_voice_ref_path", None) or "").strip()
    root = Path(settings.local_storage_root).resolve()
    has_reference = False
    if key:
        try:
            p = resolve_chatterbox_ref_to_path(key, storage_root=root)
            has_reference = path_is_readable_file(p)
        except Exception:  # noqa: BLE001
            has_reference = False
    return {
        "data": {
            "has_reference": has_reference,
            "storage_key": key or None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        },
        "meta": meta,
    }


@router.get("/chatterbox-voice-ref/content")
def get_chatterbox_voice_ref_content(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    """Binary WAV for the saved Chatterbox reference (for Studio preview)."""
    rt = resolve_runtime_settings(db, settings)
    key = (getattr(rt, "chatterbox_voice_ref_path", None) or "").strip()
    if not key:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_REFERENCE", "message": "no chatterbox reference saved for this workspace"},
        )
    root = Path(settings.local_storage_root).resolve()
    try:
        p = resolve_chatterbox_ref_to_path(key, storage_root=root)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": str(e)[:500]},
        ) from e
    if not path_is_readable_file(p):
        raise HTTPException(
            status_code=404,
            detail={"code": "MISSING_FILE", "message": "reference file missing on disk"},
        )
    return FileResponse(
        path=p,
        media_type="audio/wav",
        filename="chatterbox_reference.wav",
    )


@router.post("/chatterbox-voice-ref")
async def upload_chatterbox_voice_ref(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Upload audio, normalize to mono 24 kHz WAV via ffmpeg, save under storage, set ``chatterbox_voice_ref_path``."""
    import os
    import shutil

    tenant_id = str(settings.default_tenant_id)
    ff = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ff):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "FFMPEG_MISSING",
                "message": "ffmpeg is required on the API host to normalize voice references",
            },
        )

    raw_parts: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _CHATTERBOX_REF_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"code": "TOO_LARGE", "message": "upload exceeds 25 MB"},
            )
        raw_parts.append(chunk)
    raw = b"".join(raw_parts)
    if len(raw) < 32:
        raise HTTPException(
            status_code=422,
            detail={"code": "EMPTY", "message": "uploaded file too small"},
        )

    suffix = Path(file.filename or "upload.bin").suffix or ".bin"
    if suffix.lower() not in (
        ".wav",
        ".webm",
        ".ogg",
        ".opus",
        ".mp3",
        ".m4a",
        ".flac",
        ".aac",
        ".mp4",
    ):
        suffix = ".bin"

    src_path: str | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix) as src_tmp:
            src_path = src_tmp.name
            src_tmp.write(raw)
        root = Path(settings.local_storage_root).resolve()
        dest = voice_ref_absolute_path(storage_root=root, tenant_id=tenant_id)
        convert_upload_to_reference_wav(src_path=Path(src_path), dest_wav=dest, ffmpeg_bin=ff)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("chatterbox_ref_convert_failed", error=str(e))
        raise HTTPException(
            status_code=422,
            detail={"code": "CONVERT_FAILED", "message": str(e)[:800]},
        ) from e
    finally:
        if src_path:
            try:
                os.unlink(src_path)
            except OSError:
                pass

    key = voice_ref_storage_key(tenant_id)
    row = get_or_create_app_settings(db, tenant_id)
    prior = dict(row.config_json or {})
    prior["chatterbox_voice_ref_path"] = key
    row.config_json = sanitize_overrides(prior)
    db.commit()
    db.refresh(row)
    return {
        "data": {
            "has_reference": True,
            "storage_key": key,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        },
        "meta": meta,
    }


@router.delete("/chatterbox-voice-ref")
def delete_chatterbox_voice_ref(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Remove the saved reference file (when under this tenant’s ``voice_refs/`` dir) and clear the setting key."""
    tenant_id = str(settings.default_tenant_id)
    root = Path(settings.local_storage_root).resolve()
    tenant_dir = (root / "voice_refs" / safe_tenant_slug(tenant_id)).resolve()

    row = get_or_create_app_settings(db, tenant_id)
    prior = dict(row.config_json or {})
    key = (prior.get("chatterbox_voice_ref_path") or "").strip()
    if key:
        try:
            p = resolve_chatterbox_ref_to_path(key, storage_root=root)
            p.resolve().relative_to(tenant_dir)
            p.unlink(missing_ok=True)
        except ValueError:
            log.info("chatterbox_ref_delete_skip_not_in_tenant_dir", key=key)
        except Exception as e:  # noqa: BLE001
            log.warning("chatterbox_ref_delete_failed", key=key, error=str(e))

    prior.pop("chatterbox_voice_ref_path", None)
    row.config_json = sanitize_overrides(prior)
    db.commit()
    db.refresh(row)
    return {
        "data": {
            "has_reference": False,
            "storage_key": None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        },
        "meta": meta,
    }


# --- LLM system prompts (mirrors /v1/prompts; lives under settings for Studio + proxies) ---


@router.get("/prompts")
def list_llm_prompts_under_settings(
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


@router.put("/prompts/{prompt_key}")
def put_llm_prompt_under_settings(
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
    log.info("llm_prompt_saved", prompt_key=prompt_key, tenant_id=settings.default_tenant_id, route="settings")
    return {
        "data": {"prompt": LlmPromptItemOut.model_validate(match).model_dump(mode="json") if match else None},
        "meta": meta,
    }


class TelegramTestIn(BaseModel):
    send_test_message: bool = Field(default=True, description="Send a short message to the configured chat id")


@router.post("/telegram/test")
def test_telegram_connection(
    body: TelegramTestIn | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    _auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Validate bot token (getMe) and optionally send a test message to ``telegram_chat_id``."""
    payload = body or TelegramTestIn()
    assert_telegram_allowed(
        db=db,
        tenant_id=settings.default_tenant_id,
        auth_enabled=bool(get_settings().director_auth_enabled),
    )
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        raise HTTPException(
            status_code=400,
            detail={"code": "TELEGRAM_TOKEN_MISSING", "message": "Save a telegram_bot_token in Settings first"},
        )
    try:
        me = telegram_get_me(token)
    except Exception as exc:
        log.warning("telegram_test_getme_failed", error=str(exc))
        raise HTTPException(
            status_code=400,
            detail={"code": "TELEGRAM_TOKEN_INVALID", "message": f"Telegram getMe failed: {exc!s}"},
        ) from exc

    chat = (settings.telegram_chat_id or "").strip()
    sent = False
    if payload.send_test_message and chat:
        try:
            telegram_send_message(
                token,
                chat,
                "Directely: Telegram connection test OK.",
            )
            sent = True
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "TELEGRAM_CHAT_SEND_FAILED", "message": f"Could not send to chat id: {exc!s}"},
            ) from exc

    secret_ok = bool((settings.telegram_webhook_secret or "").strip())
    return {
        "data": {
            "ok": True,
            "bot_username": me.get("username"),
            "bot_id": me.get("id"),
            "test_message_sent": sent,
            "chat_id_configured": bool(chat),
            "webhook_secret_configured": secret_ok,
            "webhook_path": "/v1/integrations/telegram/webhook",
            "webhook_hint": "Call Telegram setWebhook with this URL (HTTPS), secret_token equal to telegram_webhook_secret, and header X-Telegram-Bot-Api-Secret-Token will be verified automatically.",
        },
        "meta": meta,
    }


@router.delete("/prompts/{prompt_key}/override")
def delete_llm_prompt_override_under_settings(
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
