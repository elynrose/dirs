"""Telegram project operator: status, stop, project list, active context (MVP)."""

from __future__ import annotations

import re
import uuid
from typing import Any

import structlog
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.db.models import AgentRun, Project, TelegramChatStudioSession
from director_api.services.agent_run_failure_copy import summarize_agent_run_failure
from director_api.services.pipeline_status import compute_pipeline_status

log = structlog.get_logger(__name__)

_OPS_NS = "_telegram_ops"
_OPS_CHAT_NS = "_telegram_ops_messages"
_ACTIVE_STATUSES = frozenset({"queued", "running", "paused"})
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "blocked"})
_STATUS_ICON = {"done": "✓", "pending": "○", "blocked": "!", "skipped": "—"}


def get_telegram_ops(row: TelegramChatStudioSession) -> dict[str, Any]:
    snap = row.brief_snapshot_json if isinstance(row.brief_snapshot_json, dict) else {}
    raw = snap.get(_OPS_NS)
    return dict(raw) if isinstance(raw, dict) else {}


def set_telegram_ops(row: TelegramChatStudioSession, ops: dict[str, Any]) -> None:
    snap = dict(row.brief_snapshot_json or {})
    snap[_OPS_NS] = {
        k: str(v).strip()
        for k, v in ops.items()
        if k in ("active_project_id", "active_agent_run_id") and v
    }
    row.brief_snapshot_json = snap
    flag_modified(row, "brief_snapshot_json")


def bind_telegram_ops_project(row: TelegramChatStudioSession, *, project_id: uuid.UUID, agent_run_id: uuid.UUID) -> None:
    set_telegram_ops(
        row,
        {
            "active_project_id": str(project_id),
            "active_agent_run_id": str(agent_run_id),
        },
    )


def reset_telegram_session_after_pipeline_start(
    row: TelegramChatStudioSession,
    *,
    project_id: uuid.UUID,
    agent_run_id: uuid.UUID,
) -> None:
    """Keep operator context; clear setup chat after RUN."""
    bind_telegram_ops_project(row, project_id=project_id, agent_run_id=agent_run_id)
    ops = get_telegram_ops(row)
    row.messages_json = []
    row.brief_snapshot_json = {_OPS_NS: ops, _OPS_CHAT_NS: []} if ops else {_OPS_CHAT_NS: []}
    flag_modified(row, "messages_json")
    flag_modified(row, "brief_snapshot_json")


def get_telegram_ops_messages(row: TelegramChatStudioSession) -> list[dict[str, str]]:
    snap = row.brief_snapshot_json if isinstance(row.brief_snapshot_json, dict) else {}
    raw = snap.get(_OPS_CHAT_NS)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for m in raw:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            out.append({"role": m["role"], "content": m["content"]})
    return out


def set_telegram_ops_messages(row: TelegramChatStudioSession, messages: list[dict[str, str]]) -> None:
    snap = dict(row.brief_snapshot_json or {})
    snap[_OPS_CHAT_NS] = messages[-_MAX_OPS_CHAT:] if len(messages) > _MAX_OPS_CHAT else messages
    row.brief_snapshot_json = snap
    flag_modified(row, "brief_snapshot_json")


_MAX_OPS_CHAT = 16


def _resolve_project_by_token(db: Session, tenant_id: str, token: str) -> Project | None:
    t = (token or "").strip().lower()
    if not t:
        return None
    try:
        pid = uuid.UUID(t)
        p = db.get(Project, pid)
        if p and p.tenant_id == tenant_id:
            return p
    except ValueError:
        pass
    rows = list(
        db.scalars(
            select(Project)
            .where(Project.tenant_id == tenant_id)
            .order_by(desc(Project.updated_at), desc(Project.created_at))
            .limit(50)
        ).all()
    )
    matches = [p for p in rows if str(p.id).lower().startswith(t)]
    if len(matches) == 1:
        return matches[0]
    return None


def _latest_agent_run(db: Session, tenant_id: str, project_id: uuid.UUID) -> AgentRun | None:
    return db.scalars(
        select(AgentRun)
        .where(AgentRun.tenant_id == tenant_id, AgentRun.project_id == project_id)
        .order_by(desc(AgentRun.created_at))
        .limit(1)
    ).first()


def _active_agent_run(db: Session, tenant_id: str, project_id: uuid.UUID) -> AgentRun | None:
    return db.scalars(
        select(AgentRun)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.project_id == project_id,
            AgentRun.status.in_(tuple(_ACTIVE_STATUSES)),
        )
        .order_by(desc(AgentRun.created_at))
        .limit(1)
    ).first()


def _default_context(
    db: Session, tenant_id: str, ops: dict[str, Any]
) -> tuple[Project | None, AgentRun | None]:
    rid = str(ops.get("active_agent_run_id") or "").strip()
    if rid:
        try:
            run = db.get(AgentRun, uuid.UUID(rid))
            if run and run.tenant_id == tenant_id:
                p = db.get(Project, run.project_id)
                if p and p.tenant_id == tenant_id:
                    return p, run
        except ValueError:
            pass

    pid = str(ops.get("active_project_id") or "").strip()
    if pid:
        try:
            p = db.get(Project, uuid.UUID(pid))
            if p and p.tenant_id == tenant_id:
                ar = _active_agent_run(db, tenant_id, p.id) or _latest_agent_run(db, tenant_id, p.id)
                return p, ar
        except ValueError:
            pass

    ar = db.scalars(
        select(AgentRun)
        .where(AgentRun.tenant_id == tenant_id, AgentRun.status.in_(tuple(_ACTIVE_STATUSES)))
        .order_by(desc(AgentRun.updated_at))
        .limit(1)
    ).first()
    if ar:
        p = db.get(Project, ar.project_id)
        if p and p.tenant_id == tenant_id:
            return p, ar

    p = db.scalars(
        select(Project)
        .where(Project.tenant_id == tenant_id)
        .order_by(desc(Project.updated_at), desc(Project.created_at))
        .limit(1)
    ).first()
    if p is None:
        return None, None
    return p, _latest_agent_run(db, tenant_id, p.id)


def classify_ops_intent(text: str) -> tuple[str, str] | None:
    """Return (intent, arg) or None if not an ops message."""
    raw = (text or "").strip()
    if not raw:
        return None
    low = raw.lower()

    if low.startswith("/"):
        parts = raw.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        mapping = {
            "/status": "status",
            "/stop": "stop",
            "/pause": "pause",
            "/resume": "resume",
            "/projects": "projects",
            "/project": "use",
            "/use": "use",
            "/retry": "retry",
            "/runs": "status",
            "/scenes": "scenes",
            "/rough": "rough",
            "/final": "final",
        }
        if cmd in mapping:
            return mapping[cmd], arg
        return None

    if low in ("status", "pipeline status", "project status"):
        return "status", ""
    if re.match(r"^(what('s| is) the )?(pipeline )?status\??$", low):
        return "status", ""
    if re.match(r"^where are we\??$", low):
        return "status", ""
    if re.match(r"^(stop|cancel)( the)?( pipeline| run)?\.?$", low):
        return "stop", ""
    if re.match(r"^pause( the)?( pipeline| run)?\.?$", low):
        return "pause", ""
    if re.match(r"^resume( the)?( pipeline| run)?\.?$", low):
        return "resume", ""
    if re.match(r"^list (projects|scenes)\.?$", low):
        return "scenes" if "scene" in low else "projects", ""
    if re.match(r"^(rough cut|export rough)\.?$", low):
        return "rough", ""
    if re.match(r"^(final cut|export final)\.?$", low):
        return "final", ""
    if re.match(r"^list projects\.?$", low):
        return "projects", ""
    if low.startswith("use project "):
        return "use", raw[12:].strip()
    if re.match(r"^retry( pipeline| run)?\.?$", low):
        return "retry", ""
    return None


def _format_steps_summary(steps: list[dict[str, Any]], *, max_lines: int = 10) -> list[str]:
    lines: list[str] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        st = str(s.get("status") or "pending")
        if st == "skipped":
            continue
        icon = _STATUS_ICON.get(st, "·")
        label = str(s.get("label") or s.get("id") or "Step")
        detail = str(s.get("detail") or "").strip()
        if detail and detail != "—":
            lines.append(f"{icon} {label} — {detail}")
        else:
            lines.append(f"{icon} {label}")
        if len(lines) >= max_lines:
            lines.append("…")
            break
    return lines


def format_ops_status_message(
    db: Session,
    settings: Any,
    *,
    tenant_id: str,
    project: Project,
    run: AgentRun | None,
) -> str:
    storage_root = getattr(settings, "local_storage_root", None)
    ps = compute_pipeline_status(
        db,
        project_id=project.id,
        tenant_id=tenant_id,
        storage_root=storage_root,
        settings=settings,
    )
    lines = [
        "Directely — status",
        f"Project: {project.title}",
        f"Id: {project.id}",
    ]
    if run:
        step = (run.current_step or "").strip() or "—"
        lines.append(f"Run: {run.status} · step {step}")
        lines.append(f"Run id: {run.id}")
        if run.status in _TERMINAL and run.error_message:
            lines.append("")
            lines.append(summarize_agent_run_failure(run.error_message)[:800])
    else:
        lines.append("Run: (no agent run yet)")

    lines.append(f"Workflow phase: {project.workflow_phase or '—'}")
    if ps.get("ok"):
        step_lines = _format_steps_summary(list(ps.get("steps") or []))
        if step_lines:
            lines.append("")
            lines.append("Pipeline:")
            lines.extend(step_lines)
        issues = ps.get("phase5_issues") or []
        if issues:
            lines.append("")
            lines.append("Export blockers:")
            for iss in issues[:4]:
                if isinstance(iss, dict):
                    lines.append(f"• {iss.get('message') or iss.get('code') or iss}")
                else:
                    lines.append(f"• {iss}")
    return "\n".join(lines)[:4090]


def format_projects_list(db: Session, tenant_id: str, *, limit: int = 8) -> str:
    rows = list(
        db.scalars(
            select(Project)
            .where(Project.tenant_id == tenant_id)
            .order_by(desc(Project.updated_at), desc(Project.created_at))
            .limit(max(1, min(limit, 20)))
        ).all()
    )
    if not rows:
        return "No projects yet. Shape a brief in chat, then send RUN to start one."
    lines = ["Directely — projects (newest first)", ""]
    for i, p in enumerate(rows, start=1):
        ar = _active_agent_run(db, tenant_id, p.id)
        run_bit = f" · run {ar.status}" if ar else ""
        lines.append(f"{i}. {p.title[:60]}")
        lines.append(f"   {str(p.id)[:8]}…{run_bit}")
    lines.append("")
    lines.append("Switch: /use <project-id-prefix>")
    return "\n".join(lines)[:4090]


def ops_help_extra() -> str:
    return (
        "\n\nProject operator:\n"
        "/status — pipeline + run status\n"
        "/projects — list recent projects\n"
        "/use <id-prefix> — focus a project\n"
        "/scenes — scene list (active project)\n"
        "/stop · /pause · /resume — run control\n"
        "/retry — continue after failure\n"
        "/rough · /final — queue export jobs\n"
        "Or ask in plain English (LLM operator when a project is active)."
    )


_INTENT_TO_ACTION: dict[str, tuple[str, str | None]] = {
    "projects": ("list_projects", None),
    "status": ("get_status", None),
    "stop": ("stop_run", None),
    "pause": ("pause_run", None),
    "resume": ("resume_run", None),
    "retry": ("retry_run", None),
    "scenes": ("list_scenes", None),
    "rough": ("enqueue_rough_cut", None),
    "final": ("enqueue_final_cut", None),
}


def try_handle_ops_message(
    db: Session,
    settings: Any,
    *,
    tenant_id: str,
    row: TelegramChatStudioSession,
    text: str,
) -> str | None:
    """Handle ops intent; return reply text or None to fall through to setup guide / agent."""
    from director_api.services.telegram_ops_actions import execute_telegram_action

    classified = classify_ops_intent(text)
    if not classified:
        return None
    intent, arg = classified

    if intent == "use":
        return execute_telegram_action(
            db,
            settings,
            tenant_id=tenant_id,
            row=row,
            action="select_project",
            args={"project_ref": arg},
        )

    spec = _INTENT_TO_ACTION.get(intent)
    if spec is None:
        return None
    action, _ = spec
    return execute_telegram_action(
        db, settings, tenant_id=tenant_id, row=row, action=action, args={}
    )
