"""Phase 4 — scene/chapter critique and critic-driven narration revision."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from director_api.agents import phase4_llm
from director_api.agents.openai_client import openai_compatible_configured
from director_api.db.models import Chapter, CriticReport, Job, Project, RevisionIssue, Scene
from director_api.services import critic_policy as critic_policy_svc
from director_api.services import phase4 as phase4_svc
from director_api.style_presets import effective_narration_style
from director_api.tasks.worker_helpers import worker_tenant_id

log = structlog.get_logger(__name__)

_WT = None


def _wt():
    global _WT
    if _WT is None:
        import director_api.tasks.worker_tasks as m

        _WT = m
    return _WT


def _persist_revision_issues(
    db: Session,
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    critic_report_id: uuid.UUID,
    scene_id: uuid.UUID | None,
    issues: list[dict[str, Any]],
) -> int:
    n = 0
    for item in issues:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "GENERIC")[:64]
        sev = str(item.get("severity") or "medium").lower()[:16]
        if sev not in ("low", "medium", "high"):
            sev = "medium"
        msg = str(item.get("message") or "")[:8000]
        refs = item.get("refs")
        db.add(
            RevisionIssue(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                critic_report_id=critic_report_id,
                scene_id=scene_id,
                asset_id=None,
                code=code,
                severity=sev,
                message=msg,
                refs_json=refs if isinstance(refs, (dict, list)) else None,
                status="open",
            )
        )
        n += 1
    return n


def _phase4_scene_critique_core(
    db: Session,
    *,
    scene_id: uuid.UUID,
    tenant_id: str,
    job_id: uuid.UUID | None,
    prior_report_id_in: uuid.UUID | None,
    settings: Any,
    meta_extra: dict[str, Any] | None = None,
    prefetched_llm: tuple[dict[str, Any] | None, list[str] | None] | None = None,
) -> dict[str, Any]:
    sc = db.get(Scene, scene_id)
    if not sc:
        raise ValueError("scene not found")
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise ValueError("chapter not found")
    project = db.get(Project, ch.project_id)
    if not project or project.tenant_id != tenant_id:
        raise ValueError("project not found")
    pol = critic_policy_svc.effective_policy(project, settings)
    payload = phase4_svc.build_scene_critique_llm_payload(db, sc)
    continuity = list(payload.get("continuity_flags") or [])
    has_image = bool(payload.get("has_approved_image"))

    dims_llm: dict[str, Any] | None = None
    recs_llm: list[str] | None = None
    if prefetched_llm is not None:
        dims_llm, recs_llm = prefetched_llm
    elif openai_compatible_configured(settings):
        llm_u: list[dict[str, Any]] = []
        dims_llm, recs_llm = phase4_llm.critique_scene_llm(payload, settings=settings, usage_sink=llm_u)
        _wt()._flush_llm_usage(db, project.tenant_id, project.id, sc.id, None, llm_u)

    score, passed, dims, issues, recs = phase4_svc.merge_heuristic_scene_critique(
        continuity_issues=continuity,
        has_approved_image=has_image,
        dimensions_llm=dims_llm,
        recommendations_llm=recs_llm,
        threshold=pol.pass_threshold,
        missing_dimension_default=pol.missing_dimension_default,
        dimension_invalid_fallback=pol.dimension_invalid_fallback,
    )

    baseline: float | None = None
    if prior_report_id_in is not None:
        prior = db.get(CriticReport, prior_report_id_in)
        if prior:
            baseline = float(prior.score)

    report_id = uuid.uuid4()
    meta = {"target": "scene", **(meta_extra or {})}
    db.add(
        CriticReport(
            id=report_id,
            tenant_id=tenant_id,
            project_id=project.id,
            target_type="scene",
            target_id=sc.id,
            job_id=job_id,
            score=float(score),
            passed=bool(passed),
            dimensions_json=dims,
            issues_json=issues,
            recommendations_json=recs,
            continuity_json={"flags": continuity},
            baseline_score=baseline,
            prior_report_id=prior_report_id_in,
            meta_json=meta,
        )
    )
    _persist_revision_issues(
        db,
        tenant_id=tenant_id,
        project_id=project.id,
        critic_report_id=report_id,
        scene_id=sc.id,
        issues=issues,
    )
    sc.critic_score = float(score)
    sc.critic_passed = bool(passed)
    db.flush()
    return {
        "critic_report_id": str(report_id),
        "scene_id": str(sc.id),
        "score": float(score),
        "passed": bool(passed),
    }


def _phase4_scene_critique(db: Session, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    scene_id = uuid.UUID(str(payload["scene_id"]))
    prior_raw = payload.get("prior_report_id")
    prior_id = uuid.UUID(str(prior_raw)) if prior_raw else None
    tenant = worker_tenant_id(job, payload)
    return _phase4_scene_critique_core(
        db,
        scene_id=scene_id,
        tenant_id=tenant,
        job_id=job.id,
        prior_report_id_in=prior_id,
        settings=settings,
    )


def _phase4_chapter_critique(db: Session, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    chapter_id = uuid.UUID(str(payload["chapter_id"]))
    tenant = worker_tenant_id(job, payload)
    ch = db.get(Chapter, chapter_id)
    if not ch:
        raise ValueError("chapter not found")
    project = db.get(Project, ch.project_id)
    if not project or project.tenant_id != tenant:
        raise ValueError("project not found")
    pol = critic_policy_svc.effective_policy(project, settings)
    scenes = list(
        db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
    )
    llm_payload = phase4_svc.build_chapter_critique_llm_payload(db, ch)
    rollup = llm_payload.get("continuity_rollup") or phase4_svc.chapter_continuity_rollup(scenes)

    dims_llm: dict[str, Any] | None = None
    recs_llm: list[str] | None = None
    if openai_compatible_configured(settings):
        llm_u: list[dict[str, Any]] = []
        dims_llm, recs_llm = phase4_llm.critique_chapter_llm(llm_payload, settings=settings, usage_sink=llm_u)
        _wt()._flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)

    score, passed, dims, issues, recs = phase4_svc.chapter_aggregate_from_scenes(
        scenes,
        target_duration_sec=ch.target_duration_sec,
        chapter_dims_llm=dims_llm,
        continuity_rollup=rollup,
        threshold_ratio=pol.chapter_min_scene_pass_ratio,
        min_aggregate_score=pol.chapter_pass_score_threshold,
        missing_dimension_default=pol.missing_dimension_default,
        dimension_invalid_fallback=pol.dimension_invalid_fallback,
    )

    report_id = uuid.uuid4()
    db.add(
        CriticReport(
            id=report_id,
            tenant_id=tenant,
            project_id=project.id,
            target_type="chapter",
            target_id=ch.id,
            job_id=job.id,
            score=float(score),
            passed=bool(passed),
            dimensions_json=dims,
            issues_json=issues,
            recommendations_json=recs,
            continuity_json=rollup if isinstance(rollup, dict) else None,
            baseline_score=None,
            prior_report_id=None,
            meta_json={"target": "chapter"},
        )
    )
    _persist_revision_issues(
        db,
        tenant_id=tenant,
        project_id=project.id,
        critic_report_id=report_id,
        scene_id=None,
        issues=issues,
    )
    ch.critic_gate_status = "passed" if passed else "blocked"
    db.flush()
    return {
        "critic_report_id": str(report_id),
        "chapter_id": str(ch.id),
        "score": float(score),
        "passed": bool(passed),
    }


def _scene_critic_revision_apply_from_latest_report(
    db: Session,
    sc: Scene,
    project: Project,
    settings: Any,
) -> bool:
    report = db.scalars(
        select(CriticReport)
        .where(
            CriticReport.project_id == project.id,
            CriticReport.tenant_id == project.tenant_id,
            CriticReport.target_type == "scene",
            CriticReport.target_id == sc.id,
        )
        .order_by(desc(CriticReport.created_at))
        .limit(1)
    ).first()
    if not report:
        return False
    recs: list[str] = []
    if isinstance(report.recommendations_json, list):
        recs = [str(x) for x in report.recommendations_json if x is not None][:12]
    if not recs and isinstance(report.issues_json, list):
        for item in report.issues_json:
            if isinstance(item, dict) and item.get("message"):
                recs.append(str(item["message"])[:500])
    if not recs:
        return False

    narration_style = effective_narration_style(
        project.narration_style, settings, db=db, tenant_id=project.tenant_id
    )
    revised: str | None = None
    if openai_compatible_configured(settings):
        llm_u: list[dict[str, Any]] = []
        revised = phase4_llm.revise_scene_narration_llm(
            purpose=sc.purpose,
            narration_text=sc.narration_text,
            recommendations=recs,
            settings=settings,
            narration_style=narration_style,
            usage_sink=llm_u,
        )
        _wt()._flush_llm_usage(db, project.tenant_id, project.id, sc.id, None, llm_u)

    if revised:
        sc.narration_text = revised
    elif sc.narration_text and recs:
        sc.narration_text = (sc.narration_text.rstrip() + " " + recs[0]).strip()[:12000]

    sc.critic_revision_count = int(sc.critic_revision_count or 0) + 1
    if sc.critic_passed is False:
        sc.critic_passed = None
    return True


def _phase4_scene_critic_revision(db: Session, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    scene_id = uuid.UUID(str(payload["scene_id"]))
    sc = db.get(Scene, scene_id)
    if not sc:
        raise ValueError("scene not found")
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise ValueError("chapter not found")
    project = db.get(Project, ch.project_id)
    if not project:
        raise ValueError("project not found")
    applied = _scene_critic_revision_apply_from_latest_report(db, sc, project, settings)
    return {"scene_id": str(sc.id), "revision_applied": applied}
