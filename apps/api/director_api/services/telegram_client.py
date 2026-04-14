"""Synchronous Telegram Bot API helpers (httpx)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 30.0
_DOCUMENT_TIMEOUT = 180.0


def _base(token: str) -> str:
    t = (token or "").strip()
    return f"https://api.telegram.org/bot{t}"


def telegram_get_me(token: str) -> dict[str, Any]:
    r = httpx.get(f"{_base(token)}/getMe", timeout=_DEFAULT_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description") or "getMe failed"))
    return data.get("result") or {}


def telegram_get_webhook_info(token: str) -> dict[str, Any]:
    """Returns Telegram's registered webhook URL and delivery diagnostics (``getWebhookInfo``)."""
    r = httpx.get(f"{_base(token)}/getWebhookInfo", timeout=_DEFAULT_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description") or "getWebhookInfo failed"))
    return data.get("result") or {}


def telegram_send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    body: dict[str, Any] = {"chat_id": chat_id, "text": (text or "")[:4096]}
    if reply_markup:
        body["reply_markup"] = reply_markup
    r = httpx.post(f"{_base(token)}/sendMessage", json=body, timeout=_DEFAULT_TIMEOUT)
    if not r.is_success:
        log.warning("telegram_send_message_http", status=r.status_code, body=r.text[:500])
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description") or "sendMessage failed"))


def telegram_send_document(
    token: str,
    chat_id: str,
    file_path: Path,
    *,
    caption: str | None = None,
) -> None:
    path = Path(file_path)
    cap = (caption or "")[:1024] if caption else None
    with path.open("rb") as f:
        files = {"document": (path.name, f)}
        data: dict[str, str] = {"chat_id": str(chat_id)}
        if cap:
            data["caption"] = cap
        r = httpx.post(
            f"{_base(token)}/sendDocument",
            data=data,
            files=files,
            timeout=_DOCUMENT_TIMEOUT,
        )
    if not r.is_success:
        log.warning("telegram_send_document_http", status=r.status_code, body=r.text[:500])
    r.raise_for_status()
    out = r.json()
    if not out.get("ok"):
        raise RuntimeError(str(out.get("description") or "sendDocument failed"))


def telegram_answer_callback_query(
    token: str,
    callback_query_id: str,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    body: dict[str, Any] = {"callback_query_id": str(callback_query_id)}
    if text:
        body["text"] = str(text)[:200]
    if show_alert:
        body["show_alert"] = True
    r = httpx.post(f"{_base(token)}/answerCallbackQuery", json=body, timeout=_DEFAULT_TIMEOUT)
    if not r.is_success:
        log.warning("telegram_answer_callback_http", status=r.status_code, body=r.text[:500])
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description") or "answerCallbackQuery failed"))
