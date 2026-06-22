"""LLM router for Telegram project operator (JSON action → execute_telegram_action)."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.orm import Session

from director_api.agents.phase2_llm import _chat_json_object_ex
from director_api.config import Settings
from director_api.db.models import TelegramChatStudioSession
from director_api.services.telegram_ops import get_telegram_ops_messages, set_telegram_ops_messages
from director_api.services.telegram_ops_actions import execute_telegram_action

log = structlog.get_logger(__name__)

_MAX_OPS_CHAT = 16

_ACTIONS_DOC = """
Available actions (pick exactly one per turn):
- none — user is shaping a NEW documentary brief (no project ops); defer to setup assistant
- get_status — pipeline + run status for active project
- list_projects — recent projects
- select_project — args: project_ref (uuid prefix or full id)
- stop_run — stop active pipeline
- pause_run — pause at next checkpoint
- resume_run — clear pause
- retry_run — continue_from_existing after failure
- enqueue_rough_cut — queue rough cut export for latest timeline
- enqueue_final_cut — queue final mix export
- list_scenes — numbered scene list for active project
- approve_scene — args: scene_ref (scene number 1..N or id prefix)
- regenerate_scene_image — args: scene_ref
"""


def _ops_context_block(ops: dict[str, Any]) -> str:
    pid = ops.get("active_project_id") or "(none)"
    rid = ops.get("active_agent_run_id") or "(none)"
    return f"Active project id: {pid}\nActive agent run id: {rid}"


def _trim_ops_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(messages) <= _MAX_OPS_CHAT:
        return messages
    return messages[-_MAX_OPS_CHAT:]


def _looks_operational(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    keys = (
        "status",
        "pipeline",
        "project",
        "stop",
        "pause",
        "resume",
        "retry",
        "rough",
        "final",
        "export",
        "scene",
        "approve",
        "regenerate",
        "image",
        "issue",
        "error",
        "stuck",
        "progress",
    )
    return any(k in low for k in keys)


def _build_agent_system(settings: Settings, ops: dict[str, Any]) -> str:
    return f"""You are the Directely Telegram project operator. The user manages documentary video pipelines from Telegram.

{_ops_context_block(ops)}
{_ACTIONS_DOC}

Respond with ONE JSON object only:
- "action" (string, required): one action name above, or "none"
- "args" (object, optional): action parameters
- "reply" (string, required): short Telegram message for the user (plain text; they will also see tool results)

Rules:
- If the user is clearly planning a NEW film (topic, tone, runtime) and has NOT asked to operate an existing project, use action "none".
- Prefer executing the right action over only chatting when they ask to stop, pause, export, approve scenes, etc.
- For ambiguous project picks, use list_projects or ask in reply while action is list_projects.
- Keep reply under 1200 characters."""


def try_telegram_ops_agent(
    db: Session,
    settings: Settings,
    *,
    tenant_id: str,
    row: TelegramChatStudioSession,
    text: str,
) -> str | None:
    """
    LLM-picked operator action. Returns reply text, or None to fall through to setup guide.
  Skips LLM when message does not look operational and there is no active project.
    """
    ops = get_telegram_ops(row)
    has_project = bool(str(ops.get("active_project_id") or "").strip())
    if not has_project and not _looks_operational(text):
        return None

    prior = get_telegram_ops_messages(row)
    prior.append({"role": "user", "content": text})
    if len(prior) > _MAX_OPS_CHAT:
        prior = prior[-_MAX_OPS_CHAT:]

    flat = "\n\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in prior
    )

    data, err = _chat_json_object_ex(
        settings,
        system=_build_agent_system(settings, ops),
        user=flat,
        service_type="telegram_ops_agent",
        temperature=0.2,
    )
    if data is None:
        log.warning("telegram_ops_agent_llm_failed", error=err)
        return None

    action = str(data.get("action") or "none").strip().lower()
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    reply = str(data.get("reply") or "").strip()

    if action in ("", "none", "chat"):
        return None

    try:
        result = execute_telegram_action(
            db, settings, tenant_id=tenant_id, row=row, action=action, args=args
        )
    except Exception as exc:
        log.warning("telegram_ops_agent_action_failed", action=action, error=str(exc))
        result = f"Action failed: {exc!s}"

    if result is None:
        return None

    out = result
    if reply and reply not in result:
        out = f"{reply}\n\n{result}" if result else reply

    prior.append({"role": "assistant", "content": out[:4000]})
    set_telegram_ops_messages(row, prior)
    db.add(row)
    db.commit()

    return out[:4090]
