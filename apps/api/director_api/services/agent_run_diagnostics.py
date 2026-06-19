"""Plain-text diagnostics for failed or blocked agent runs (UI technical log download)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import AgentRun, AppSetting, Asset, Chapter, Project, Scene


def _summarize_steps(steps: list[Any]) -> list[str]:
    """Collapse noisy progress events into phase outcome lines."""
    if not steps:
        return ["(no step events recorded)"]
    # Last event per (step, status) bucket — keep terminal outcomes.
    terminal: dict[str, dict[str, Any]] = {}
    progress_counts: dict[str, int] = {}
    for ev in steps:
        if not isinstance(ev, dict):
            continue
        step = str(ev.get("step") or ev.get("phase") or "unknown")
        status = str(ev.get("status") or "")
        if status == "progress":
            progress_counts[step] = progress_counts.get(step, 0) + 1
            continue
        key = f"{step}:{status}"
        terminal[key] = ev
    out: list[str] = []
    for key in sorted(terminal.keys()):
        ev = terminal[key]
        step, status = key.split(":", 1)
        extra_bits: list[str] = []
        for field in ("reason", "error_code", "message", "scene_total", "min_clips_per_scene", "failure_reason_summary", "summary", "heal_kind"):
            v = ev.get(field)
            if v is not None and str(v).strip():
                extra_bits.append(f"{field}={v}")
        prog = progress_counts.get(step, 0)
        prog_note = f" ({prog} progress events)" if prog else ""
        suffix = f" — {', '.join(extra_bits)}" if extra_bits else ""
        out.append(f"  {step}: {status}{prog_note}{suffix}")
    return out or ["(only progress events; no terminal step outcomes)"]


def _project_media_stats(db: Session, project_id) -> dict[str, Any]:
    scene_count = int(
        db.scalar(
            select(func.count())
            .select_from(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
        )
        or 0
    )
    img_ok = int(
        db.scalar(
            select(func.count())
            .select_from(Asset)
            .join(Scene, Asset.scene_id == Scene.id)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(
                Chapter.project_id == project_id,
                Asset.asset_type == "image",
                Asset.status == "succeeded",
            )
        )
        or 0
    )
    vid_ok = int(
        db.scalar(
            select(func.count())
            .select_from(Asset)
            .join(Scene, Asset.scene_id == Scene.id)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(
                Chapter.project_id == project_id,
                Asset.asset_type == "video",
                Asset.status == "succeeded",
            )
        )
        or 0
    )
    failed = int(
        db.scalar(
            select(func.count())
            .select_from(Asset)
            .join(Scene, Asset.scene_id == Scene.id)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id, Asset.status == "failed")
        )
        or 0
    )
    return {
        "scene_count": scene_count,
        "succeeded_images": img_ok,
        "succeeded_videos": vid_ok,
        "failed_assets": failed,
    }


def _top_asset_failure_reasons(db: Session, project_id, *, limit: int = 8) -> list[tuple[int, str]]:
    rows = db.execute(
        select(Asset.error_message, func.count())
        .select_from(Asset)
        .join(Scene, Asset.scene_id == Scene.id)
        .join(Chapter, Scene.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id, Asset.status == "failed")
        .group_by(Asset.error_message)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    out: list[tuple[int, str]] = []
    for msg, n in rows:
        text = str(msg or "").strip()
        if not text:
            continue
        out.append((int(n), text[:200]))
    return out


def _workspace_providers(db: Session, settings: Settings) -> dict[str, str]:
    row = db.scalars(select(AppSetting).limit(1)).first()
    cfg = row.config_json if row and isinstance(row.config_json, dict) else {}
    return {
        "active_text_provider": str(cfg.get("active_text_provider") or settings.active_text_provider or ""),
        "active_image_provider": str(cfg.get("active_image_provider") or settings.active_image_provider or ""),
        "active_video_provider": str(cfg.get("active_video_provider") or settings.active_video_provider or ""),
        "comfyui_base_url": str(getattr(settings, "comfyui_base_url", "") or ""),
    }


def build_agent_run_diagnostics_text(db: Session, run: AgentRun, settings: Settings) -> str:
    lines: list[str] = [
        "Directely — automation run technical log",
        f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}",
        "",
        "=== Run ===",
        f"Run ID: {run.id}",
        f"Project ID: {run.project_id}",
        f"Status: {run.status}",
        f"Current step: {run.current_step or '—'}",
        f"Block code: {run.block_code or '—'}",
        f"Started: {run.started_at.isoformat() if run.started_at else '—'}",
        f"Completed: {run.completed_at.isoformat() if run.completed_at else '—'}",
        "",
        "=== Error (full) ===",
        (run.error_message or "(none)").strip(),
        "",
    ]
    if run.block_message:
        lines.extend(["=== Block message ===", run.block_message.strip(), ""])

    po = run.pipeline_options_json if isinstance(run.pipeline_options_json, dict) else {}
    lines.extend(
        [
            "=== Pipeline options ===",
            json.dumps(po, indent=2, default=str),
            "",
        ]
    )

    project = db.get(Project, run.project_id) if run.project_id else None
    if project:
        lines.extend(
            [
                "=== Project ===",
                f"Title: {project.title}",
                f"Topic: {(project.topic or '')[:500]}",
                f"Workflow phase: {project.workflow_phase}",
                f"Preferred image provider: {project.preferred_image_provider or '—'}",
                f"Preferred video provider: {project.preferred_video_provider or '—'}",
                "",
            ]
        )
        stats = _project_media_stats(db, project.id)
        lines.extend(
            [
                "=== Media on project ===",
                f"Scenes: {stats['scene_count']}",
                f"Succeeded images: {stats['succeeded_images']}",
                f"Succeeded videos: {stats['succeeded_videos']}",
                f"Failed asset rows: {stats['failed_assets']}",
                "",
            ]
        )
        reasons = _top_asset_failure_reasons(db, project.id)
        if reasons:
            lines.append("=== Top asset failure reasons ===")
            for n, msg in reasons:
                lines.append(f"  ({n}x) {msg}")
            lines.append("")

    providers = _workspace_providers(db, settings)
    lines.extend(
        [
            "=== Workspace providers ===",
            f"Text: {providers['active_text_provider'] or '—'}",
            f"Image: {providers['active_image_provider'] or '—'}",
            f"Video: {providers['active_video_provider'] or '—'}",
            f"COMFYUI_BASE_URL: {providers['comfyui_base_url'] or '—'}",
            "",
        ]
    )

    steps = run.steps_json if isinstance(run.steps_json, list) else []
    lines.append("=== Step outcomes ===")
    lines.extend(_summarize_steps(steps))
    lines.extend(
        [
            "",
            "=== Local worker log (if self-hosted) ===",
            "See .run/director-worker.log on the machine running the Celery worker.",
            "",
        ]
    )
    return "\n".join(lines)


def user_facing_run_failure_summary(error_message: str | None) -> str:
    """Short plain-English summary for API clients (mirrors web summarizeAgentRunFailure)."""
    from director_api.services.agent_run_failure_copy import summarize_agent_run_failure

    return summarize_agent_run_failure(error_message)
