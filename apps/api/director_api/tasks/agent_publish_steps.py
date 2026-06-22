"""Agent-run blocks for thumbnail, opening hook, and optional outro."""

from __future__ import annotations

from typing import Any, Callable, Literal
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from director_api.db.models import AgentRun, Project
from director_api.services import agent_resume as agent_resume_svc
from director_api.services import pipeline_oversight as pipeline_oversight_svc
from director_api.services.publish_hook import append_hook_scene
from director_api.services.publish_outro import append_outro_scene, resolve_include_outro_scene
from director_api.services.publish_pack import opening_hook_core, thumbnail_core

log = structlog.get_logger(__name__)

StepResult = Literal["ok", "halt", "fail"]


def _agent_publish_step(
    db: Session,
    *,
    run: AgentRun,
    aid: UUID,
    agent_run_id: str,
    step_key: str,
    would_skip: bool,
    run_core: Callable[[], None],
    next_step: str,
    cont: bool,
    oversight_earliest: str | None,
    force_steps: frozenset[str],
    halt: Callable[[], bool],
    wt: Any,
) -> StepResult:
    if halt():
        return "halt"
    if pipeline_oversight_svc.effective_resume_skip_with_force(
        cont, oversight_earliest, step_key, would_skip, force_steps
    ):
        run = db.get(AgentRun, aid)
        if run:
            wt._append_event(run, step_key, "skipped", reason="already_done")
            run.current_step = next_step
            db.commit()
        return "ok"
    try:
        run = db.get(AgentRun, aid)
        if not run:
            log.error("agent_run_missing", agent_run_id=agent_run_id)
            return "fail"
        if halt():
            return "halt"
        wt._append_event(run, step_key, "running")
        db.commit()
        run_core()
        db.commit()
        run = db.get(AgentRun, aid)
        if run:
            wt._append_event(run, step_key, "succeeded")
            run.current_step = next_step
            db.commit()
        return "ok"
    except Exception as e:  # noqa: BLE001
        run = db.get(AgentRun, aid)
        if run:
            wt._agent_run_mark_failed(db, run, step_key, e)
        log.exception(f"agent_run_{step_key}_failed", agent_run_id=agent_run_id)
        return "fail"


def run_agent_thumbnail_step(
    db: Session,
    *,
    run: AgentRun,
    aid: UUID,
    agent_run_id: str,
    project: Project,
    settings: Any,
    cont: bool,
    oversight_earliest: str | None,
    force_steps: frozenset[str],
    halt: Callable[[], bool],
    wt: Any,
) -> StepResult:
    return _agent_publish_step(
        db,
        run=run,
        aid=aid,
        agent_run_id=agent_run_id,
        step_key="thumbnail",
        would_skip=agent_resume_svc.should_skip_thumbnail(cont, project),
        run_core=lambda: thumbnail_core(db, project, settings),
        next_step="opening_hook",
        cont=cont,
        oversight_earliest=oversight_earliest,
        force_steps=force_steps,
        halt=halt,
        wt=wt,
    )


def run_agent_opening_hook_step(
    db: Session,
    *,
    run: AgentRun,
    aid: UUID,
    agent_run_id: str,
    project: Project,
    settings: Any,
    cont: bool,
    oversight_earliest: str | None,
    force_steps: frozenset[str],
    halt: Callable[[], bool],
    wt: Any,
) -> StepResult:
    return _agent_publish_step(
        db,
        run=run,
        aid=aid,
        agent_run_id=agent_run_id,
        step_key="opening_hook",
        would_skip=agent_resume_svc.should_skip_opening_hook(cont, project),
        run_core=lambda: opening_hook_core(db, project, settings),
        next_step="scenes",
        cont=cont,
        oversight_earliest=oversight_earliest,
        force_steps=force_steps,
        halt=halt,
        wt=wt,
    )


def run_agent_hook_scene_step(
    db: Session,
    *,
    run: AgentRun,
    aid: UUID,
    agent_run_id: str,
    project: Project,
    settings: Any,
    cont: bool,
    oversight_earliest: str | None,
    force_steps: frozenset[str],
    halt: Callable[[], bool],
    wt: Any,
) -> StepResult:
    return _agent_publish_step(
        db,
        run=run,
        aid=aid,
        agent_run_id=agent_run_id,
        step_key="hook_scene",
        would_skip=agent_resume_svc.should_skip_hook_scene(cont, project, db),
        run_core=lambda: append_hook_scene(db, project, settings),
        next_step="outro",
        cont=cont,
        oversight_earliest=oversight_earliest,
        force_steps=force_steps,
        halt=halt,
        wt=wt,
    )


def run_agent_outro_step(
    db: Session,
    *,
    run: AgentRun,
    aid: UUID,
    agent_run_id: str,
    project: Project,
    settings: Any,
    pipeline_options: dict[str, Any],
    cont: bool,
    oversight_earliest: str | None,
    force_steps: frozenset[str],
    halt: Callable[[], bool],
    wt: Any,
) -> StepResult:
    include = resolve_include_outro_scene(project, pipeline_options)
    if not include:
        run = db.get(AgentRun, aid)
        if run:
            wt._append_event(run, "outro", "skipped", reason="include_outro_disabled")
            run.current_step = "story_research_review"
            db.commit()
        return "ok"
    return _agent_publish_step(
        db,
        run=run,
        aid=aid,
        agent_run_id=agent_run_id,
        step_key="outro",
        would_skip=agent_resume_svc.should_skip_outro(cont, project, db, include_outro=True),
        run_core=lambda: append_outro_scene(db, project, settings),
        next_step="story_research_review",
        cont=cont,
        oversight_earliest=oversight_earliest,
        force_steps=force_steps,
        halt=halt,
        wt=wt,
    )
