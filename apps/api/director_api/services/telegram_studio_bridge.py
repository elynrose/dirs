"""Telegram ↔ Chat Studio setup guide: session state, brief merge, RUN trigger, ProjectCreate."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.api.schemas.project import ProjectCreate
from director_api.db.models import TelegramChatStudioSession
from director_api.services.chat_studio_guide import _sanitize_brief_patch

_MAX_MESSAGES = 48

# Whole-message triggers (case-insensitive). LLM instructs user to send RUN alone.
_PIPELINE_TRIGGER_TOKENS = frozenset({"run", "go", "start"})


def is_pipeline_trigger_message(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in _PIPELINE_TRIGGER_TOKENS


def parse_standalone_frame_aspect(text: str) -> str | None:
    """If the user sent only 16:9 or 9:16 (optional spaces), return that token; else None."""
    t = re.sub(r"\s+", "", (text or "").strip().lower())
    if t in ("16:9", "9:16"):
        return t
    return None


def merge_brief_snapshot(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base) if isinstance(base, dict) else {}
    clean = _sanitize_brief_patch(patch)
    for k, v in clean.items():
        if v is not None:
            out[k] = v
    return out


def trim_chat_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(messages) <= _MAX_MESSAGES:
        return messages
    return messages[-_MAX_MESSAGES:]


def project_create_from_brief_snapshot(snap: dict[str, Any]) -> ProjectCreate:
    title = (snap.get("title") or "").strip() or "Telegram project"
    topic_raw = (snap.get("topic") or "").strip()
    topic = topic_raw if topic_raw else title
    tr_raw = snap.get("target_runtime_minutes")
    try:
        tr = int(tr_raw) if tr_raw is not None else 10
    except (TypeError, ValueError):
        tr = 10
    tr = max(2, min(120, tr))
    kwargs: dict[str, Any] = {
        "title": title[:500],
        "topic": topic[:8000],
        "target_runtime_minutes": tr,
    }
    for key in (
        "audience",
        "tone",
        "visual_style",
        "narration_style",
        "music_preference",
        "preferred_text_provider",
        "preferred_image_provider",
        "preferred_video_provider",
        "preferred_speech_provider",
    ):
        v = snap.get(key)
        if isinstance(v, str) and v.strip():
            kwargs[key] = v.strip()
    fs = snap.get("factual_strictness")
    if fs in ("strict", "balanced", "creative"):
        kwargs["factual_strictness"] = fs
    rs = snap.get("research_min_sources")
    try:
        if rs is not None:
            rsv = int(rs)
            if 1 <= rsv <= 100:
                kwargs["research_min_sources"] = rsv
    except (TypeError, ValueError):
        pass
    bl = snap.get("budget_limit")
    if isinstance(bl, (int, float)):
        kwargs["budget_limit"] = float(bl)
    far = snap.get("frame_aspect_ratio")
    if isinstance(far, str) and far.strip() in ("16:9", "9:16"):
        kwargs["frame_aspect_ratio"] = far.strip()
    return ProjectCreate(**kwargs)


def get_telegram_studio_session_row(db: Session, tenant_id: str, telegram_chat_id: str) -> TelegramChatStudioSession | None:
    tid = (tenant_id or "").strip()
    cid = str(telegram_chat_id or "").strip()
    if not tid or not cid:
        return None
    return db.scalar(
        select(TelegramChatStudioSession).where(
            TelegramChatStudioSession.tenant_id == tid,
            TelegramChatStudioSession.telegram_chat_id == cid,
        )
    )


def get_or_create_telegram_studio_session(
    db: Session, tenant_id: str, telegram_chat_id: str
) -> TelegramChatStudioSession:
    row = get_telegram_studio_session_row(db, tenant_id, telegram_chat_id)
    if row is not None:
        return row
    row = TelegramChatStudioSession(
        tenant_id=(tenant_id or "").strip(),
        telegram_chat_id=str(telegram_chat_id or "").strip(),
        messages_json=[],
        brief_snapshot_json={},
    )
    db.add(row)
    db.flush()
    return row


def validate_brief_for_pipeline(pc: ProjectCreate) -> None:
    """Raise HTTPException 422 if JSON schema validation would fail on enqueue."""
    from director_api.validation.brief import validate_documentary_brief

    try:
        validate_documentary_brief(pc.brief_dict())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail={"code": "BRIEF_INCOMPLETE", "message": str(e)},
        ) from e
