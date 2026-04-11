"""Telegram Bot API webhook: Chat Studio dialogue, then RUN triggers hands-off pipeline."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.api.schemas.agent_run import AgentRunCreate
from director_api.api.schemas.project import ProjectCreate
from director_api.config import Settings, get_settings
from director_api.db.models import AppSetting, AgentRun, TelegramChatStudioSession
from director_api.db.session import get_db
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.services.telegram_client import telegram_send_message
from director_api.services.telegram_studio_bridge import (
    get_or_create_telegram_studio_session,
    get_telegram_studio_session_row,
    is_pipeline_trigger_message,
    merge_brief_snapshot,
    project_create_from_brief_snapshot,
    trim_chat_messages,
    validate_brief_for_pipeline,
)
from director_api.services.chat_studio_guide import run_setup_guide_turn
from director_api.services.tenant_entitlements import (
    assert_agent_run_pipeline_allowed,
    assert_can_create_project,
)

router = APIRouter(prefix="/integrations/telegram", tags=["integrations"])
log = structlog.get_logger(__name__)

_TELEGRAM_MAX_OUT = 4096


def _help_text() -> str:
    return (
        "Directely (Telegram)\n\n"
        "We use the same Chat Studio assistant as the web app to shape your documentary brief.\n"
        "Reply with details about your topic; when things are ready, the assistant will ask you to send RUN alone "
        "to start the full hands-off pipeline to final video.\n\n"
        "Shortcuts: send RUN, GO, or START alone to queue the pipeline (after you've discussed the brief).\n\n"
        "Commands:\n"
        "/start — this help\n"
        "/help — this help"
    )


def _normalize_chat_id(raw: Any, expected: str) -> bool:
    return str(raw).strip() == str(expected).strip()


def _effective_webhook_secret(rt: Settings, base: Settings) -> str:
    """Tenant merge may omit secrets; fall back to env on ``base``."""
    return (rt.telegram_webhook_secret or base.telegram_webhook_secret or "").strip()


def _find_runtime_settings_for_telegram_chat(
    db: Session,
    base: Settings,
    *,
    incoming_chat_id: str,
    secret_header: str,
) -> Settings | None:
    """Resolve the workspace whose saved ``telegram_chat_id`` matches this update and secret matches setWebhook.

    Telegram does not send a logged-in user; we bind the chat to the tenant that configured that chat id in Studio.
    """
    inc = str(incoming_chat_id).strip()
    sh = (secret_header or "").strip()
    if not inc:
        return None

    def secret_matches(rt: Settings) -> bool:
        eff = _effective_webhook_secret(rt, base)
        if not sh:
            return not eff
        if not eff:
            return False
        return sh == eff

    for app in db.scalars(select(AppSetting)).all():
        tid = (app.tenant_id or "").strip()
        if not tid:
            continue
        rt = resolve_runtime_settings(db, base, tid)
        if str(rt.telegram_chat_id or "").strip() != inc:
            continue
        if not (rt.telegram_bot_token or "").strip():
            continue
        if secret_matches(rt):
            return rt

    tid0 = (base.default_tenant_id or "").strip()
    if tid0:
        rt = resolve_runtime_settings(db, base, tid0)
        if (
            str(rt.telegram_chat_id or "").strip() == inc
            and (rt.telegram_bot_token or "").strip()
            and secret_matches(rt)
        ):
            return rt

    return None


def _truncate_telegram(s: str) -> str:
    t = (s or "").strip()
    if len(t) <= _TELEGRAM_MAX_OUT:
        return t
    return t[: _TELEGRAM_MAX_OUT - 1] + "…"


def _enqueue_pipeline_from_brief(
    db: Session,
    rt: Settings,
    *,
    brief: ProjectCreate,
    auth_on: bool,
) -> tuple[Any, Any]:
    """Create project + agent run; returns (project, agent_run). Raises HTTPException."""
    from director_api.api.routers.agent_runs import _project_from_brief

    assert_can_create_project(db, rt.default_tenant_id, auth_enabled=auth_on)
    create_body = AgentRunCreate(
        brief=brief,
        pipeline_options={
            "continue_from_existing": False,
            "through": "full_video",
            "unattended": True,
        },
    )
    assert_agent_run_pipeline_allowed(
        dict(create_body.pipeline_options or {}),
        db=db,
        tenant_id=rt.default_tenant_id,
        auth_enabled=auth_on,
    )
    p = _project_from_brief(db, rt, create_body)
    po: dict = dict(create_body.pipeline_options or {})
    po["continue_from_existing"] = False
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=rt.default_tenant_id,
        project_id=p.id,
        started_by_user_id=None,
        status="queued",
        steps_json=[],
        pipeline_options_json=po,
        pipeline_control_json={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    from director_api.tasks.worker_tasks import run_agent_run as run_agent_run_task

    run_agent_run_task.delay(str(run.id))
    return p, run


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict[str, bool]:
    base = get_settings()
    auth_on = bool(base.director_auth_enabled)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "BAD_JSON", "message": "expected JSON body"}) from None

    msg = body.get("message") or body.get("edited_message")
    if not isinstance(msg, dict):
        return {"ok": True}

    chat = msg.get("chat")
    chat_id_raw = chat.get("id") if isinstance(chat, dict) else None
    if chat_id_raw is None:
        return {"ok": True}
    incoming_chat = str(chat_id_raw).strip()

    sh = (x_telegram_bot_api_secret_token or "").strip()
    rt = _find_runtime_settings_for_telegram_chat(db, base, incoming_chat_id=incoming_chat, secret_header=sh)
    if rt is None:
        log.warning(
            "telegram_webhook_no_matching_tenant",
            incoming_chat=incoming_chat,
            hint="Save Telegram settings in Studio for this workspace; chat id and webhook secret must match setWebhook",
        )
        return {"ok": True}

    # Do not call assert_telegram_allowed here: the request is already scoped by webhook secret + matching
    # telegram_chat_id. The default env tenant id often differs from the workspace row in app_settings; gating on
    # entitlements for the wrong id caused silent 200s. Saving Telegram in Settings still requires the entitlement.

    token = (rt.telegram_bot_token or "").strip()
    chat_expected = (rt.telegram_chat_id or "").strip()
    if not token or not chat_expected:
        log.warning("telegram_webhook_missing_token_or_chat", tenant_id=rt.default_tenant_id)
        return {"ok": True}

    if not _normalize_chat_id(incoming_chat, chat_expected):
        log.info("telegram_webhook_chat_mismatch", got=incoming_chat, expected=chat_expected)
        return {"ok": True}

    text_raw = msg.get("text")
    text = text_raw.strip() if isinstance(text_raw, str) else ""
    if not text:
        return {"ok": True}

    tenant_id = rt.default_tenant_id
    chat_key = str(chat_expected).strip()

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        if cmd in ("/start", "/help"):
            try:
                telegram_send_message(token, chat_expected, _help_text())
            except Exception as exc:
                log.warning("telegram_help_send_failed", error=str(exc))
        return {"ok": True}

    if is_pipeline_trigger_message(text):
        row = get_telegram_studio_session_row(db, tenant_id, chat_key)
        snap: dict[str, Any] = dict(row.brief_snapshot_json or {}) if row else {}
        try:
            pc = project_create_from_brief_snapshot(snap)
            validate_brief_for_pipeline(pc)
        except HTTPException as e:
            try:
                detail = e.detail
                if isinstance(detail, dict):
                    msg_err = str(detail.get("message", detail))
                else:
                    msg_err = str(detail)
                telegram_send_message(
                    token,
                    chat_expected,
                    _truncate_telegram(
                        "Brief is not ready yet. Keep chatting with the assistant about your documentary, "
                        f"then send RUN when you're ready.\n\n({msg_err})"
                    ),
                )
            except Exception:
                pass
            return {"ok": True}

        try:
            p, run = _enqueue_pipeline_from_brief(db, rt, brief=pc, auth_on=auth_on)
        except HTTPException as e:
            db.rollback()
            try:
                detail = e.detail
                if isinstance(detail, dict):
                    msg_err = str(detail.get("message", detail))
                else:
                    msg_err = str(detail)
                telegram_send_message(token, chat_expected, _truncate_telegram(f"Could not start run: {msg_err}"))
            except Exception:
                pass
            return {"ok": True}
        except Exception as exc:
            log.exception("telegram_enqueue_failed", error=str(exc))
            db.rollback()
            try:
                telegram_send_message(token, chat_expected, _truncate_telegram(f"Failed to queue run: {exc!s}"))
            except Exception:
                pass
            return {"ok": True}

        if row is not None:
            stale = db.get(TelegramChatStudioSession, row.id)
            if stale is not None:
                db.delete(stale)
        db.commit()

        log.info("telegram_agent_run_enqueued", agent_run_id=str(run.id), project_id=str(p.id))
        try:
            telegram_send_message(
                token,
                chat_expected,
                _truncate_telegram(f"Queued full pipeline run.\nProject: {p.title}\nRun id: {run.id}"),
            )
        except Exception as exc:
            log.warning("telegram_ack_send_failed", error=str(exc))
        return {"ok": True}

    # Chat Studio turn
    row = get_or_create_telegram_studio_session(db, tenant_id, chat_key)
    raw_messages = list(row.messages_json or [])
    messages: list[dict[str, str]] = []
    for m in raw_messages:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": text})
    messages = trim_chat_messages(messages)

    brief_snapshot: dict[str, Any] = dict(row.brief_snapshot_json or {})
    data, err = run_setup_guide_turn(
        rt, messages=messages, brief_snapshot=brief_snapshot, telegram_mode=True
    )
    if err or not data:
        log.warning("telegram_chat_studio_llm_failed", error=err)
        try:
            telegram_send_message(
                token,
                chat_expected,
                "Sorry, the assistant could not reply right now. Try again in a moment.",
            )
        except Exception:
            pass
        return {"ok": True}

    reply_text = (data.get("reply") or "").strip() or "…"
    patch = data.get("brief_patch") if isinstance(data.get("brief_patch"), dict) else {}
    merged = merge_brief_snapshot(brief_snapshot, patch)

    messages.append({"role": "assistant", "content": reply_text})
    messages = trim_chat_messages(messages)

    row.messages_json = messages
    row.brief_snapshot_json = merged
    flag_modified(row, "messages_json")
    flag_modified(row, "brief_snapshot_json")
    db.add(row)
    db.commit()

    try:
        telegram_send_message(token, chat_expected, _truncate_telegram(reply_text))
    except Exception as exc:
        log.warning("telegram_reply_send_failed", error=str(exc))

    return {"ok": True}
