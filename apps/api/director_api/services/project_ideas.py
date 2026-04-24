"""Generate, persist, and run project ideas (LLM topic → titles/descriptions; schedule pipeline)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from director_api.agents.phase2_llm import _chat_json_object_ex
from director_api.api.schemas.project import ProjectCreate
from director_api.api.schemas.project_ideas import IdeaItem
from director_api.config import Settings, get_settings
from director_api.db.models import AgentRun, IdeaScheduledRun, Project, ProjectIdea
from director_api.services.agent_resume import normalize_pipeline_options_for_persist
from director_api.services.runtime_settings import get_or_create_app_settings, resolve_runtime_settings
from director_api.services.tenant_entitlements import assert_agent_run_pipeline_allowed, assert_can_create_project
from director_api.services.project_frame import coerce_clip_frame_fit
from director_api.validation.brief import validate_documentary_brief

log = structlog.get_logger(__name__)

_IDEAS_SYSTEM = """You help filmmakers brainstorm documentary-style video projects.
Return a single JSON object with key "ideas" (array of 4–6 objects).
Each object must have "title" (short catchy working title, max 120 chars) and "description" (2–4 sentences: angle, audience hook, what the video covers — max 600 words total across all descriptions combined).
Themes must be factual/educational or narrative documentary; avoid disallowed content."""


def generate_idea_items(settings: Settings, topic: str) -> tuple[list[IdeaItem], str | None]:
    """LLM: topic → structured ideas. Returns (items, error_message)."""
    user = f'Topic to explore:\n"""{topic.strip()}"""\n\nRespond with JSON only: {{"ideas":[{{"title":"...","description":"..."}},...]}}'
    data, err = _chat_json_object_ex(
        settings,
        system=_IDEAS_SYSTEM,
        user=user,
        service_type="project_ideas_generate",
        temperature=0.45,
    )
    if err or not isinstance(data, dict):
        return [], err or "llm_empty"
    raw_ideas = data.get("ideas")
    if not isinstance(raw_ideas, list):
        return [], "ideas_not_array"
    out: list[IdeaItem] = []
    for x in raw_ideas[:8]:
        if not isinstance(x, dict):
            continue
        t = str(x.get("title") or "").strip()
        d = str(x.get("description") or "").strip()
        if len(t) < 1 or len(d) < 1:
            continue
        try:
            out.append(IdeaItem(title=t[:500], description=d[:8000]))
        except Exception:
            continue
    if not out:
        return [], "no_valid_ideas"
    return out, None


def _brief_dict_from_workspace(
    *,
    title: str,
    topic: str,
    target_runtime_minutes: int,
    rs: Settings,
    config_json: dict[str, Any],
) -> dict[str, Any]:
    nar_ref = str(config_json.get("default_narration_style_ref") or "").strip()
    nar_preset = str(
        config_json.get("narration_style_preset") or "calm_documentary",
    ).strip()
    narration_style = (
        nar_ref
        if nar_ref.startswith("preset:") or nar_ref.startswith("user:")
        else f"preset:{nar_preset or 'calm_documentary'}"
    )
    vis = str(config_json.get("visual_style_preset") or "cinematic_documentary").strip()
    frame_ar = config_json.get("frame_aspect_ratio") or "16:9"
    if frame_ar not in ("16:9", "9:16"):
        frame_ar = "16:9"
    d: dict[str, Any] = {
        "title": title,
        "topic": topic,
        "target_runtime_minutes": target_runtime_minutes,
        "audience": "general",
        "tone": "documentary",
        "narration_style": narration_style,
        "visual_style": f"preset:{vis or 'cinematic_documentary'}",
        "frame_aspect_ratio": frame_ar,
    }
    _active_map = {
        "preferred_text_provider": "active_text_provider",
        "preferred_image_provider": "active_image_provider",
        "preferred_video_provider": "active_video_provider",
        "preferred_speech_provider": "active_speech_provider",
    }
    for pk, ak in _active_map.items():
        v = getattr(rs, ak, None)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            d[pk] = s
    return d


def _default_pipeline_options(rs: Settings) -> dict[str, Any]:
    po: dict[str, Any] = {
        "through": "full_video",
        "continue_from_existing": False,
        "rerun_web_research": False,
        "narration_granularity": "scene",
        "auto_generate_scene_images": bool(getattr(rs, "agent_run_auto_generate_scene_images", True)),
        "auto_generate_scene_videos": bool(getattr(rs, "agent_run_auto_generate_scene_videos", False)),
        "min_scene_images": int(getattr(rs, "agent_run_min_scene_images", 1) or 1),
        "min_scene_videos": int(getattr(rs, "agent_run_min_scene_videos", 1) or 1),
        "auto_images_max_concurrency": max(
            1,
            min(8, int(getattr(rs, "agent_run_auto_images_max_concurrency", 1) or 1)),
        ),
    }
    ps = str(getattr(rs, "agent_run_pipeline_speed", "standard") or "standard").strip().lower()
    if ps in ("demo_fast", "production_heavy"):
        po["pipeline_speed"] = ps
    return normalize_pipeline_options_for_persist(po)


def create_project_and_start_agent_run(
    db: Session,
    *,
    settings: Settings,
    tenant_id: str,
    title: str,
    topic: str,
    target_runtime_minutes: int,
    started_by_user_id: int | None,
    auth_enabled: bool,
) -> tuple[Project, AgentRun]:
    """Create project + queued agent run (same contract as POST /v1/agent-runs with brief)."""
    tid = (tenant_id or "").strip() or settings.default_tenant_id
    assert_can_create_project(db, tid, auth_enabled=auth_enabled)
    rs = resolve_runtime_settings(db, settings, tenant_id=tid)
    cfg = get_or_create_app_settings(db, tid).config_json or {}
    if not isinstance(cfg, dict):
        cfg = {}
    brief_d = _brief_dict_from_workspace(
        title=title,
        topic=topic,
        target_runtime_minutes=target_runtime_minutes,
        rs=rs,
        config_json=cfg,
    )
    validate_documentary_brief(brief_d)
    b = ProjectCreate.model_validate(brief_d)

    p = Project(
        tenant_id=tid,
        title=b.title,
        topic=b.topic,
        status="draft",
        research_min_sources=b.research_min_sources if b.research_min_sources is not None else 3,
        target_runtime_minutes=b.target_runtime_minutes,
        audience=b.audience,
        tone=b.tone,
        visual_style=b.visual_style,
        narration_style=b.narration_style,
        factual_strictness=b.factual_strictness,
        music_preference=b.music_preference,
        budget_limit=b.budget_limit,
        preferred_text_provider=b.preferred_text_provider,
        preferred_image_provider=b.preferred_image_provider,
        preferred_video_provider=b.preferred_video_provider,
        preferred_speech_provider=b.preferred_speech_provider,
        frame_aspect_ratio=(b.frame_aspect_ratio or "16:9"),
        clip_frame_fit=coerce_clip_frame_fit(getattr(b, "clip_frame_fit", None)),
    )
    db.add(p)
    db.flush()

    po = _default_pipeline_options(rs)
    assert_agent_run_pipeline_allowed(po, db=db, tenant_id=tid, auth_enabled=auth_enabled)

    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=p.id,
        started_by_user_id=started_by_user_id,
        status="queued",
        steps_json=[],
        pipeline_options_json=po,
        pipeline_control_json={},
    )
    db.add(run)
    db.commit()
    db.refresh(p)
    db.refresh(run)
    from director_api.tasks.worker_tasks import run_agent_run

    run_agent_run.delay(str(run.id))
    log.info("idea_agent_run_enqueued", agent_run_id=str(run.id), project_id=str(p.id))
    return p, run


def process_due_scheduled_idea_runs(db: Session) -> dict[str, Any]:
    """Mark due schedules and enqueue one agent run per idea (called from Celery beat)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    rows = list(
        db.scalars(
            select(IdeaScheduledRun)
            .where(
                IdeaScheduledRun.status == "pending",
                IdeaScheduledRun.scheduled_at <= now,
            )
            .order_by(IdeaScheduledRun.scheduled_at)
            .limit(50)
        ).all()
    )
    n_ok = 0
    n_fail = 0
    auth_on = bool(get_settings().director_auth_enabled)
    for row in rows:
        idea = db.get(ProjectIdea, row.idea_id)
        if not idea or idea.tenant_id != row.tenant_id:
            row.status = "failed"
            row.error_message = "idea_missing"
            n_fail += 1
            continue
        try:
            _, ar = create_project_and_start_agent_run(
                db,
                settings=settings,
                tenant_id=row.tenant_id,
                title=idea.title,
                topic=idea.description,
                target_runtime_minutes=10,
                started_by_user_id=row.created_by_user_id,
                auth_enabled=auth_on,
            )
            row.status = "completed"
            row.agent_run_id = ar.id
            row.error_message = None
            n_ok += 1
        except Exception as e:  # noqa: BLE001
            row.status = "failed"
            row.error_message = str(e)[:2000]
            n_fail += 1
            log.warning("scheduled_idea_run_failed", idea_id=str(row.idea_id), error=str(e)[:400])
    if n_ok or n_fail:
        db.commit()
    return {"processed": len(rows), "completed": n_ok, "failed": n_fail}


def list_ideas_for_tenant(db: Session, tenant_id: str, *, limit: int = 100) -> list[ProjectIdea]:
    return list(
        db.scalars(
            select(ProjectIdea)
            .where(ProjectIdea.tenant_id == tenant_id)
            .order_by(desc(ProjectIdea.updated_at))
            .limit(max(1, min(limit, 200)))
        ).all()
    )


def cancel_pending_schedules_for_idea(db: Session, tenant_id: str, idea_id: uuid.UUID) -> int:
    rows = list(
        db.scalars(
            select(IdeaScheduledRun).where(
                IdeaScheduledRun.tenant_id == tenant_id,
                IdeaScheduledRun.idea_id == idea_id,
                IdeaScheduledRun.status == "pending",
            )
        ).all()
    )
    for r in rows:
        r.status = "cancelled"
    if rows:
        db.commit()
    return len(rows)
