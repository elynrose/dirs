"""Telegram Bot API webhook: drive agent runs and receive updates via configured bot + chat."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from director_api.api.schemas.agent_run import AgentRunCreate
from director_api.api.schemas.project import ProjectCreate
from director_api.config import get_settings
from director_api.services.tenant_entitlements import assert_telegram_allowed
from director_api.db.models import AgentRun
from director_api.db.session import get_db
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.services.telegram_client import telegram_send_message

router = APIRouter(prefix="/integrations/telegram", tags=["integrations"])
log = structlog.get_logger(__name__)


def _help_text() -> str:
    return (
        "Directely bot\n\n"
        "Send a message with your documentary brief (topic). The first line is used as the title.\n"
        "The pipeline runs unattended through final video.\n\n"
        "Commands:\n"
        "/start — this help\n"
        "/help — this help"
    )


def _normalize_chat_id(raw: Any, expected: str) -> bool:
    return str(raw).strip() == str(expected).strip()


def _brief_from_message_text(text: str) -> ProjectCreate:
    t = (text or "").strip()
    if len(t) < 3:
        raise ValueError("brief too short (need at least a few characters)")
    first_line = t.split("\n", 1)[0].strip() or "Telegram video"
    title = first_line if len(first_line) <= 500 else first_line[:497] + "..."
    topic = t[:8000]
    return ProjectCreate(title=title, topic=topic, target_runtime_minutes=10)


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict[str, bool]:
    base = get_settings()
    rt = resolve_runtime_settings(db, base, None)
    assert_telegram_allowed(
        db=db, tenant_id=rt.default_tenant_id, auth_enabled=bool(base.director_auth_enabled)
    )

    secret = (rt.telegram_webhook_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "TELEGRAM_WEBHOOK_SECRET_MISSING",
                "message": "Set telegram_webhook_secret in Settings and pass the same value to Telegram setWebhook secret_token",
            },
        )
    if (x_telegram_bot_api_secret_token or "").strip() != secret:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "invalid webhook secret"},
        )

    token = (rt.telegram_bot_token or "").strip()
    chat_expected = (rt.telegram_chat_id or "").strip()
    if not token or not chat_expected:
        raise HTTPException(
            status_code=503,
            detail={"code": "TELEGRAM_NOT_CONFIGURED", "message": "Configure telegram_bot_token and telegram_chat_id in Settings"},
        )

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "BAD_JSON", "message": "expected JSON body"}) from None

    msg = body.get("message") or body.get("edited_message")
    if not isinstance(msg, dict):
        return {"ok": True}

    chat = msg.get("chat")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if chat_id is None or not _normalize_chat_id(chat_id, chat_expected):
        log.info("telegram_webhook_chat_mismatch", got=chat_id)
        return {"ok": True}

    text_raw = msg.get("text")
    text = text_raw.strip() if isinstance(text_raw, str) else ""
    if not text:
        return {"ok": True}

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        if cmd in ("/start", "/help"):
            try:
                telegram_send_message(token, chat_expected, _help_text())
            except Exception as exc:
                log.warning("telegram_help_send_failed", error=str(exc))
        return {"ok": True}

    try:
        brief = _brief_from_message_text(text)
    except ValueError as e:
        try:
            telegram_send_message(token, chat_expected, f"Could not start run: {e}")
        except Exception:
            pass
        return {"ok": True}

    from director_api.api.routers.agent_runs import _project_from_brief

    create_body = AgentRunCreate(
        brief=brief,
        pipeline_options={
            "continue_from_existing": False,
            "through": "full_video",
            "unattended": True,
        },
    )
    try:
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
        log.info("telegram_agent_run_enqueued", agent_run_id=str(run.id), project_id=str(p.id))
        try:
            telegram_send_message(
                token,
                chat_expected,
                f"Queued full pipeline run.\nProject: {p.title}\nRun id: {run.id}",
            )
        except Exception as exc:
            log.warning("telegram_ack_send_failed", error=str(exc))
    except Exception as exc:
        log.exception("telegram_enqueue_failed", error=str(exc))
        db.rollback()
        try:
            telegram_send_message(token, chat_expected, f"Failed to queue run: {exc!s}"[:4096])
        except Exception:
            pass

    return {"ok": True}
