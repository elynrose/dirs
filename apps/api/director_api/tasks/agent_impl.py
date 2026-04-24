"""Agent run orchestration — media tail, checkpointing, and Celery body.

Runtime symbols still owned by ``worker_tasks`` are accessed via :func:`_wt` to avoid import cycles.
"""
from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, func, select

from director_api.agents import phase4_llm
from director_api.db.models import (
    AgentRun,
    Asset,
    Chapter,
    CriticReport,
    Job,
    NarrationTrack,
    Project,
    ProjectCharacter,
    Scene,
    TimelineVersion,
)
from director_api.db.session import SessionLocal
from director_api.logging_config import get_logger
from director_api.services import agent_resume as agent_resume_svc
from director_api.services import phase3 as phase3_svc
from director_api.services import pipeline_oversight as pipeline_oversight_svc
from director_api.services.character_prompt import character_bible_for_llm_context, character_consistency_prefix
from director_api.services.phase5_readiness import compute_phase5_readiness, raise_phase5_gate
from director_api.services.scene_coverage import coverage_visual_slots_needed, pick_coverage_payload
from director_api.services.llm_prompt_runtime import llm_prompt_map_scope
from director_api.services.llm_prompt_service import build_resolved_prompt_map
from director_api.services.scene_timeline_duration import (
    effective_scene_visual_budget_sec,
)
from director_api.services.pexels_scene_fill import maybe_fill_pexels_for_project_scenes
from director_api.services.timeline_image_repair import list_export_ready_scene_visuals_ordered
from director_api.services.research_service import sanitize_jsonb_text
from director_api.tasks.agent_exceptions import AgentRunBlocked, AgentRunStopRequested
from director_api.tasks.phase3_impl import (
    _phase3_image_generate,
    _phase3_scene_still_job_succeeded,
    _phase3_scenes_plan_for_chapter,
    _phase3_video_generate,
)
from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export
from director_api.validation.timeline_schema import validate_timeline_document

log = get_logger(__name__)

_WT = None


def _wt():
    """Late bind to ``worker_tasks`` after it has finished loading."""
    global _WT
    if _WT is None:
        import director_api.tasks.worker_tasks as m

        _WT = m
    return _WT


def _agent_run_mark_failed(db, run: AgentRun, step: str, exc: Exception) -> None:
    run.status = "failed"
    run.current_step = None
    run.error_message = str(exc)[:8000]
    run.completed_at = datetime.now(timezone.utc)
    _wt()._append_event(run, step, "failed", error_code="EXCEPTION", message=str(exc)[:500])
    db.commit()


def _synthetic_job(
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    jtype: str,
    payload: dict[str, Any],
) -> Job:
    return Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        type=jtype,
        status="queued",
        payload=payload,
        project_id=project_id,
    )


def _scene_has_succeeded_image(db, scene_id: uuid.UUID) -> bool:
    n = db.scalar(
        select(func.count())
        .select_from(Asset)
        .where(
            Asset.scene_id == scene_id,
            Asset.asset_type == "image",
            Asset.status == "succeeded",
        )
    )
    return int(n or 0) > 0


def _scene_succeeded_image_count(db, scene_id: uuid.UUID) -> int:
    n = db.scalar(
        select(func.count())
        .select_from(Asset)
        .where(
            Asset.scene_id == scene_id,
            Asset.asset_type == "image",
            Asset.status == "succeeded",
        )
    )
    return int(n or 0)


def _scene_succeeded_video_count(db, scene_id: uuid.UUID) -> int:
    n = db.scalar(
        select(func.count())
        .select_from(Asset)
        .where(
            Asset.scene_id == scene_id,
            Asset.asset_type == "video",
            Asset.status == "succeeded",
        )
    )
    return int(n or 0)


def _scene_has_succeeded_video(db, scene_id: uuid.UUID) -> bool:
    n = db.scalar(
        select(func.count())
        .select_from(Asset)
        .where(
            Asset.scene_id == scene_id,
            Asset.asset_type == "video",
            Asset.status == "succeeded",
        )
    )
    return int(n or 0) > 0


def _auto_scene_coverage_pass(
    db,
    settings: Any,
    *,
    project_id: uuid.UUID,
    tenant_id: str,
    all_scenes: list[Scene],
    agent_run_uuid: uuid.UUID,
    automation_character_prefix: str | None,
) -> bool | None:
    """When ``agent_run_auto_scene_coverage_clips`` is on, enqueue extra image/video takes until each scene has enough clips vs VO.

    Returns ``None`` if the user stopped the run; ``True`` otherwise.
    """
    if not bool(getattr(settings, "agent_run_auto_scene_coverage_clips", False)):
        return True
    storage_root = Path(settings.local_storage_root).resolve()
    ffprobe_bin = (getattr(settings, "ffprobe_bin", None) or "ffprobe").strip() or "ffprobe"
    timeout_tl = float(settings.ffmpeg_timeout_sec)
    clip_sec = float(_wt()._scene_clip_duration_sec(settings))
    prefer_video = bool(getattr(settings, "agent_run_auto_generate_scene_videos", False))
    extra_total = 0
    for sc in all_scenes:
        if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
            return None
        budget = effective_scene_visual_budget_sec(
            db,
            scene=sc,
            project_id=project_id,
            base_clip_sec=clip_sec,
            storage_root=storage_root,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_tl,
            tail_padding_sec=_wt()._scene_vo_tail_padding_sec(settings),
        )
        need = coverage_visual_slots_needed(budget_sec=budget, clip_sec=clip_sec)
        have = _scene_succeeded_image_count(db, sc.id) + _scene_succeeded_video_count(db, sc.id)
        deficit = max(0, need - have)
        for i in range(deficit):
            if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                return None
            cov = pick_coverage_payload(take_index=have + i + int(sc.order_index or 0) * 97)
            # WAN / image-to-video: do not run coverage video until at least one scene still exists.
            use_video = prefer_video and _scene_has_succeeded_image(db, sc.id)
            if use_video:
                payload_v: dict[str, Any] = {
                    "scene_id": str(sc.id),
                    "tenant_id": tenant_id,
                    "generation_tier": "preview",
                    "agent_run_id": str(agent_run_uuid),
                    "video_prompt_override": cov["video_prompt_override"],
                    "exclude_character_bible": bool(cov.get("exclude_character_bible")),
                }
                if automation_character_prefix:
                    payload_v["_automation_character_prefix"] = automation_character_prefix
                jv = _synthetic_job(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    jtype="scene_generate_video",
                    payload=payload_v,
                )
                try:
                    vout = _phase3_video_generate(db, jv)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "auto_scene_coverage_video_failed",
                        scene_id=str(sc.id),
                        error=str(e)[:500],
                    )
                    db.rollback()
                    continue
                if isinstance(vout, dict) and vout.get("stopped"):
                    return None
                if isinstance(vout, dict) and vout.get("ok") is True:
                    aid_s = vout.get("asset_id")
                    if aid_s:
                        ast = db.get(Asset, uuid.UUID(str(aid_s)))
                        if ast and ast.status == "succeeded" and ast.approved_at is None:
                            ast.approved_at = datetime.now(timezone.utc)
                    extra_total += 1
                db.commit()
            else:
                payload_i: dict[str, Any] = {
                    "scene_id": str(sc.id),
                    "tenant_id": tenant_id,
                    "generation_tier": "preview",
                    "agent_run_id": str(agent_run_uuid),
                    "image_prompt_override": cov["image_prompt_override"],
                    "exclude_character_bible": bool(cov.get("exclude_character_bible")),
                }
                if automation_character_prefix:
                    payload_i["_automation_character_prefix"] = automation_character_prefix
                j_img = _synthetic_job(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    jtype="scene_generate_image",
                    payload=payload_i,
                )
                try:
                    out = _phase3_image_generate(db, j_img)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "auto_scene_coverage_image_failed",
                        scene_id=str(sc.id),
                        error=str(e)[:500],
                    )
                    db.rollback()
                    continue
                if isinstance(out, dict) and out.get("stopped"):
                    return None
                if not _phase3_scene_still_job_succeeded(out, db):
                    db.commit()
                    continue
                aid_s = out.get("asset_id") if isinstance(out, dict) else None
                if aid_s:
                    ast = db.get(Asset, uuid.UUID(str(aid_s)))
                    if ast and ast.status == "succeeded" and ast.approved_at is None:
                        ast.approved_at = datetime.now(timezone.utc)
                extra_total += 1
                db.commit()
    log.info("auto_scene_coverage_pass_done", project_id=str(project_id), extra_assets=extra_total)
    return True


def _scene_has_visual_media_for_auto(db, scene_id: uuid.UUID) -> bool:
    """True if the scene already has a succeeded image or video (auto pipeline should not add more stills)."""
    return _scene_has_succeeded_image(db, scene_id) or _scene_has_succeeded_video(db, scene_id)


def _scene_ids_with_succeeded_visual_media(db, scene_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Scene ids that already have at least one succeeded image or video (single query vs per-scene checks)."""
    if not scene_ids:
        return set()
    rows = db.scalars(
        select(Asset.scene_id)
        .where(
            Asset.scene_id.in_(scene_ids),
            Asset.status == "succeeded",
            Asset.asset_type.in_(("image", "video")),
        )
        .distinct()
    ).all()
    return {sid for sid in rows if sid is not None}


def _project_has_character_rows(db, project_id: uuid.UUID) -> bool:
    n = db.scalar(
        select(func.count()).select_from(ProjectCharacter).where(ProjectCharacter.project_id == project_id)
    ) or 0
    return int(n) > 0


def _auto_pipeline_approve_scene_image(db: Any, sc: Scene) -> None:
    """Set approved_at on the primary succeeded scene image when it is still unapproved (auto-tail convention)."""
    if not _scene_has_succeeded_image(db, sc.id):
        return
    approved_img = db.scalars(
        select(Asset)
        .where(
            Asset.scene_id == sc.id,
            Asset.asset_type == "image",
            Asset.status == "succeeded",
            Asset.approved_at.is_not(None),
        )
        .order_by(desc(Asset.approved_at), desc(Asset.created_at))
        .limit(1)
    ).first()
    if approved_img is not None:
        return
    newest_img = db.scalars(
        select(Asset)
        .where(
            Asset.scene_id == sc.id,
            Asset.asset_type == "image",
            Asset.status == "succeeded",
        )
        .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
        .limit(1)
    ).first()
    if newest_img is not None and newest_img.approved_at is None:
        newest_img.approved_at = datetime.now(timezone.utc)


def _chapter_has_chapter_narration_audio(db: Any, chapter_id: uuid.UUID) -> bool:
    row = db.scalar(
        select(NarrationTrack.id)
        .where(NarrationTrack.chapter_id == chapter_id, NarrationTrack.scene_id.is_(None))
        .where(NarrationTrack.audio_url.isnot(None))
        .limit(1)
    )
    return row is not None


def _scene_has_scene_narration_audio(db, scene_id: uuid.UUID) -> bool:
    row = db.scalar(
        select(NarrationTrack.id)
        .where(
            NarrationTrack.scene_id == scene_id,
            NarrationTrack.audio_url.isnot(None),
        )
        .limit(1)
    )
    return row is not None


def _ordered_scenes_for_project(db, project_id: uuid.UUID) -> list[Scene]:
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)).all()
    )
    out: list[Scene] = []
    for ch in chapters:
        scenes = list(
            db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
        )
        out.extend(scenes)
    return out


def _ensure_scene_plans_for_scripted_chapters_missing_scenes(
    db,
    project: Project,
    settings: Any,
    agent_run_uuid: uuid.UUID,
) -> bool:
    """
    Before auto image/video: any chapter with a real script but zero scene rows gets a full plan.

    Automate/hands-off sometimes skips per-chapter planning (resume rules, short-script edge cases, or
    ordering); the media tail should still cover every scripted chapter.
    """
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)).all()
    )
    ensured = 0
    scene_plan_char_prefix: str | None = None
    scene_plan_char_bible: str | None = None
    for ch in chapters:
        if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
            return False
        if len((ch.script_text or "").strip()) < 12:
            continue
        if not phase3_svc.chapter_eligible_for_scene_planning(ch):
            continue
        n_sc = db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch.id)) or 0
        if int(n_sc) > 0:
            continue
        if scene_plan_char_prefix is None:
            scene_plan_char_prefix = character_consistency_prefix(db, project.id, max_chars=2000)
            scene_plan_char_bible = character_bible_for_llm_context(db, project.id, max_chars=6000)
        try:
            _phase3_scenes_plan_for_chapter(
                db,
                ch,
                project,
                settings,
                cached_character_consistency_prefix=scene_plan_char_prefix,
                cached_character_bible_for_llm=scene_plan_char_bible,
            )
            ensured += 1
            db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning(
                "full_video_tail_scene_plan_ensure_failed",
                chapter_id=str(ch.id),
                error=str(e)[:800],
            )
            db.rollback()
    if ensured:
        log.info(
            "full_video_tail_scene_plans_ensured",
            project_id=str(project.id),
            chapters_planned=ensured,
        )
    return True


def _run_agent_full_pipeline_tail(
    db,
    run: AgentRun,
    agent_run_uuid: uuid.UUID,
    project: Project,
    settings: Any,
    *,
    tail_resume_from: str | None = None,
    force_steps: frozenset[str] | None = None,
) -> bool:
    """After story/research review: character bible, images, narration, timeline, rough_cut, final_cut (sync). Returns False if user stopped."""
    fs = force_steps or frozenset()
    force_regen_characters = "auto_characters" in fs
    force_regen_images = "auto_images" in fs
    force_regen_videos = "auto_videos" in fs
    force_regen_narration = "auto_narration" in fs
    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
        return False
    tenant_id = project.tenant_id
    pid = project.id
    if not _ensure_scene_plans_for_scripted_chapters_missing_scenes(db, project, settings, agent_run_uuid):
        return False
    project = db.get(Project, pid)
    if not project:
        raise ValueError("project missing before full-video media tail")
    all_scenes = _ordered_scenes_for_project(db, pid)
    if not all_scenes:
        raise ValueError("FULL_VIDEO_NO_SCENES")

    run = db.get(AgentRun, agent_run_uuid)
    run_opts_pre = run.pipeline_options_json if isinstance(run.pipeline_options_json, dict) else {}
    allow_unapproved_media = bool(run_opts_pre.get("unattended"))
    if "auto_generate_scene_videos" in run_opts_pre:
        auto_scene_videos_pre = bool(run_opts_pre.get("auto_generate_scene_videos"))
    else:
        auto_scene_videos_pre = bool(getattr(settings, "agent_run_auto_generate_scene_videos", False))
    if "auto_generate_scene_images" in run_opts_pre:
        auto_scene_images_pre = bool(run_opts_pre.get("auto_generate_scene_images"))
    else:
        auto_scene_images_pre = bool(getattr(settings, "agent_run_auto_generate_scene_images", True))

    def _clamp_min_scene_media(n: Any) -> int:
        try:
            return max(1, min(10, int(n)))
        except (TypeError, ValueError):
            return 1

    min_scene_images = _clamp_min_scene_media(
        run_opts_pre.get("min_scene_images", getattr(settings, "agent_run_min_scene_images", 1))
    )
    min_scene_videos = _clamp_min_scene_media(
        run_opts_pre.get("min_scene_videos", getattr(settings, "agent_run_min_scene_videos", 1))
    )

    def _clamp_auto_images_concurrency(n: Any) -> int:
        try:
            return max(1, min(8, int(n)))
        except (TypeError, ValueError):
            return 1

    auto_images_max_concurrency = _clamp_auto_images_concurrency(
        run_opts_pre.get(
            "auto_images_max_concurrency",
            getattr(settings, "agent_run_auto_images_max_concurrency", 1),
        )
    )

    tr = pipeline_oversight_svc.normalize_tail_resume(
        tail_resume_from,
        auto_scene_videos=auto_scene_videos_pre,
        auto_scene_images=auto_scene_images_pre,
    )
    hard_floor = pipeline_oversight_svc.compute_hard_tail_floor(
        db,
        pid,
        [s.id for s in all_scenes],
        auto_generate_scene_images=auto_scene_images_pre,
        auto_generate_scene_videos=auto_scene_videos_pre,
        min_scene_images=min_scene_images,
        min_scene_videos=min_scene_videos,
    )
    tr = pipeline_oversight_svc.clamp_tail_resume_to_hard_floor(tr, hard_floor)

    tail_wall_t0 = time.perf_counter()
    log.info(
        "agent_full_video_tail_timing",
        phase="start",
        agent_run_id=str(agent_run_uuid),
        project_id=str(pid),
        scene_count=len(all_scenes),
        min_scene_images=min_scene_images,
        min_scene_videos=min_scene_videos,
        auto_generate_scene_images=auto_scene_images_pre,
        auto_generate_scene_videos=auto_scene_videos_pre,
        applied_pipeline_speed=run_opts_pre.get("_applied_pipeline_speed"),
        tail_resume=str(tr) if tr is not None else None,
    )

    # Character bible (LLM) — image/video prompts use consistency prefixes from ProjectCharacter rows.
    # Run when oversight allows this tail slot, or whenever we still have no ProjectCharacter rows — do not
    # require ``tail_should_run(auto_images)`` only: oversight may point past ``auto_characters`` while work is
    # still pending, which would otherwise skip bible generation entirely.
    char_tail_ok = pipeline_oversight_svc.tail_should_run_with_force("auto_characters", tr, fs)
    need_character_gen = force_regen_characters or not _project_has_character_rows(db, pid)
    if char_tail_ok or need_character_gen:
        run = db.get(AgentRun, agent_run_uuid)
        if need_character_gen:
            if run:
                run.current_step = "auto_characters"
                _wt()._append_event(run, "auto_characters", "running")
            db.commit()
            try:
                proj_for_chars = db.get(Project, pid)
                if not proj_for_chars:
                    raise ValueError("project missing before character bible generation")
                _wt()._characters_generate_core(db, proj_for_chars, settings)
                db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                run = db.get(AgentRun, agent_run_uuid)
                if run:
                    _wt()._agent_run_mark_failed(db, run, "auto_characters", e)
                raise
            run = db.get(AgentRun, agent_run_uuid)
            if run:
                _wt()._append_event(run, "auto_characters", "succeeded")
                db.commit()
        else:
            if run:
                _wt()._append_event(run, "auto_characters", "skipped", reason="characters_already_present")
            db.commit()
    else:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            _wt()._append_event(run, "auto_characters", "skipped", reason="oversight_tail_resume")
        db.commit()

    project = db.get(Project, pid)
    if not project:
        raise ValueError("project missing after character bible step")

    # One read per tail pass — reused for every automation scene_generate_image (sequential + parallel threads).
    automation_tail_character_prefix = character_consistency_prefix(db, pid, max_chars=2000)

    if pipeline_oversight_svc.tail_should_run_with_force("auto_narration", tr, fs):
        all_scenes_narr = _ordered_scenes_for_project(db, pid)
        narr_scene_targets: list[Scene] = []
        for sc in all_scenes_narr:
            if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                return False
            if len((sc.narration_text or "").strip()) < 2:
                continue
            if _scene_has_scene_narration_audio(db, sc.id) and not force_regen_narration:
                continue
            narr_scene_targets.append(sc)

        run = db.get(AgentRun, agent_run_uuid)
        if run:
            run.current_step = "auto_narration"
            _wt()._append_event(
                run,
                "auto_narration",
                "running",
                scene_narration_targets=len(narr_scene_targets),
            )
        db.commit()

        def _auto_scene_narration_pass(target_scenes: list[Scene]) -> list[uuid.UUID] | None:
            failed_s: list[uuid.UUID] = []
            n_targets = len(target_scenes)
            for si, sc in enumerate(target_scenes):
                if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                    return None
                if _scene_has_scene_narration_audio(db, sc.id) and not force_regen_narration:
                    continue
                # Per-scene heartbeat for Studio (same idea as ``scenes`` chapter progress): auto narration
                # runs inline without Celery Job rows, so ``updated_at`` + progress events must advance during TTS.
                run_hb = db.get(AgentRun, agent_run_uuid)
                if run_hb:
                    _wt()._append_event(
                        run_hb,
                        "auto_narration",
                        "progress",
                        scene_index=int(si + 1),
                        scenes_total=int(n_targets),
                    )
                    db.commit()
                js = _synthetic_job(
                    tenant_id=tenant_id,
                    project_id=pid,
                    jtype="narration_generate_scene",
                    payload={
                        "scene_id": str(sc.id),
                        "tenant_id": tenant_id,
                        "agent_run_id": str(agent_run_uuid),
                    },
                )
                try:
                    ns_out = _wt()._narration_generate_scene(db, js, settings)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "auto_pipeline_narration_scene_failed",
                        scene_id=str(sc.id),
                        error=str(e)[:800],
                    )
                    failed_s.append(sc.id)
                    db.commit()
                    continue
                if isinstance(ns_out, dict) and ns_out.get("stopped"):
                    return None
                db.commit()
            return failed_s

        narr_failed_scenes = _auto_scene_narration_pass(narr_scene_targets)
        if narr_failed_scenes is None:
            return False
        if narr_failed_scenes:
            log.warning(
                "auto_pipeline_narration_scene_retry",
                project_id=str(pid),
                failed_scene_count=len(narr_failed_scenes),
            )
            run = db.get(AgentRun, agent_run_uuid)
            if run:
                _wt()._append_event(
                    run,
                    "auto_narration",
                    "retry",
                    failed_scene_count=len(narr_failed_scenes),
                    failed_scene_ids=[str(x) for x in narr_failed_scenes[:64]],
                )
                db.commit()
            retry_scenes = [s for s in narr_scene_targets if s.id in set(narr_failed_scenes)]
            narr_failed_scenes2 = _auto_scene_narration_pass(retry_scenes)
            if narr_failed_scenes2 is None:
                return False
            if narr_failed_scenes2:
                raise ValueError(
                    "AUTO_NARRATION_FAILED_SCENES_AFTER_RETRY: "
                    + ",".join(str(x) for x in narr_failed_scenes2[:32])
                )
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            _wt()._append_event(run, "auto_narration", "succeeded", narration_granularity="scene")
        db.commit()
    else:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            _wt()._append_event(run, "auto_narration", "skipped", reason="oversight_tail_resume")
        db.commit()

    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
        return False

    # Initial pass + several retries so flaky providers are less likely to leave scenes without images.
    _AUTO_SCENE_MEDIA_MAX_PASSES = 5

    def _auto_image_pass(target_scenes: list[Scene]) -> list[uuid.UUID] | None:
        """Return scene ids still missing enough succeeded stills after this pass; None if user stopped."""

        def _gen_one_image(sc: Scene) -> str:
            """ok | fail | stop"""
            j_img = _synthetic_job(
                tenant_id=tenant_id,
                project_id=pid,
                jtype="scene_generate_image",
                payload={
                    "scene_id": str(sc.id),
                    "tenant_id": tenant_id,
                    "generation_tier": "preview",
                    "agent_run_id": str(agent_run_uuid),
                    "_automation_character_prefix": automation_tail_character_prefix,
                },
            )
            try:
                out = _phase3_image_generate(db, j_img)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "auto_pipeline_image_scene_exception",
                    scene_id=str(sc.id),
                    error=str(e)[:800],
                )
                return "fail"
            if isinstance(out, dict) and out.get("stopped"):
                return "stop"
            if not _phase3_scene_still_job_succeeded(out, db):
                return "fail"
            aid_s = out.get("asset_id") if isinstance(out, dict) else None
            if aid_s:
                ast = db.get(Asset, uuid.UUID(str(aid_s)))
                if ast and ast.status == "succeeded" and ast.approved_at is None:
                    ast.approved_at = datetime.now(timezone.utc)
            return "ok"

        def _parallel_gen_scene_still(scene_id: uuid.UUID) -> str:
            """One still in an isolated session (thread-safe). Returns ok | fail | stop."""
            with SessionLocal() as tdb:
                if _wt()._agent_run_checkpoint(tdb, agent_run_uuid) == "stop":
                    return "stop"
                j_img = _synthetic_job(
                    tenant_id=tenant_id,
                    project_id=pid,
                    jtype="scene_generate_image",
                    payload={
                        "scene_id": str(scene_id),
                        "tenant_id": tenant_id,
                        "generation_tier": "preview",
                        "agent_run_id": str(agent_run_uuid),
                        "_automation_character_prefix": automation_tail_character_prefix,
                    },
                )
                try:
                    out = _phase3_image_generate(tdb, j_img)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "auto_pipeline_image_scene_exception",
                        scene_id=str(scene_id),
                        error=str(e)[:800],
                    )
                    try:
                        tdb.rollback()
                    except Exception:
                        pass
                    return "fail"
                if isinstance(out, dict) and out.get("stopped"):
                    return "stop"
                if not _phase3_scene_still_job_succeeded(out, tdb):
                    try:
                        tdb.commit()
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            "auto_pipeline_image_thread_commit_failed",
                            scene_id=str(scene_id),
                            error=str(e)[:400],
                        )
                        try:
                            tdb.rollback()
                        except Exception:
                            pass
                    return "fail"
                aid_s = out.get("asset_id") if isinstance(out, dict) else None
                if aid_s:
                    ast = tdb.get(Asset, uuid.UUID(str(aid_s)))
                    if ast and ast.status == "succeeded" and ast.approved_at is None:
                        ast.approved_at = datetime.now(timezone.utc)
                try:
                    tdb.commit()
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "auto_pipeline_image_thread_commit_failed",
                        scene_id=str(scene_id),
                        error=str(e)[:400],
                    )
                    try:
                        tdb.rollback()
                    except Exception:
                        pass
                    return "fail"
                return "ok"

        img_conc = int(auto_images_max_concurrency)
        if img_conc <= 1:
            failed_ids: list[uuid.UUID] = []
            for sc in target_scenes:
                if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                    return None
                scene_failed = False
                while _scene_succeeded_image_count(db, sc.id) < min_scene_images:
                    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                        return None
                    g = _gen_one_image(sc)
                    if g == "stop":
                        return None
                    if g == "fail":
                        failed_ids.append(sc.id)
                        scene_failed = True
                        break
                    db.commit()
                if scene_failed:
                    continue
                if force_regen_images:
                    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                        return None
                    g = _gen_one_image(sc)
                    if g == "stop":
                        return None
                    if g == "fail":
                        failed_ids.append(sc.id)
                        db.commit()
                        continue
                    db.commit()
                _auto_pipeline_approve_scene_image(db, sc)
                if _scene_succeeded_image_count(db, sc.id) < min_scene_images:
                    failed_ids.append(sc.id)
                db.commit()
            return failed_ids

        failed_ids_p: list[uuid.UUID] = []
        failed_set: set[uuid.UUID] = set()

        while True:
            if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                return None
            unders = [
                sc
                for sc in target_scenes
                if sc.id not in failed_set and _scene_succeeded_image_count(db, sc.id) < min_scene_images
            ]
            if not unders:
                break
            chunk = unders[:img_conc]
            log.info(
                "auto_pipeline_images_parallel_round",
                project_id=str(pid),
                concurrency=len(chunk),
                scene_ids=[str(sc.id) for sc in chunk],
            )
            with ThreadPoolExecutor(max_workers=min(img_conc, len(chunk))) as pool:
                futures = {pool.submit(_parallel_gen_scene_still, sc.id): sc for sc in chunk}
                for fut in as_completed(futures):
                    sc = futures[fut]
                    try:
                        g = fut.result()
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            "auto_pipeline_image_parallel_future",
                            scene_id=str(sc.id),
                            error=str(e)[:500],
                        )
                        g = "fail"
                    if g == "stop":
                        return None
                    if g == "fail":
                        failed_set.add(sc.id)
                        failed_ids_p.append(sc.id)
            db.expire_all()

        if force_regen_images:
            regen = [sc for sc in target_scenes if sc.id not in failed_set]
            for i in range(0, len(regen), img_conc):
                if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                    return None
                batch = regen[i : i + img_conc]
                log.info(
                    "auto_pipeline_images_parallel_force_regen",
                    project_id=str(pid),
                    batch=len(batch),
                    scene_ids=[str(s.id) for s in batch],
                )
                with ThreadPoolExecutor(max_workers=min(img_conc, len(batch))) as pool:
                    futures = {pool.submit(_parallel_gen_scene_still, sc.id): sc for sc in batch}
                    for fut in as_completed(futures):
                        sc = futures[fut]
                        try:
                            g = fut.result()
                        except Exception as e:  # noqa: BLE001
                            log.warning(
                                "auto_pipeline_image_parallel_force_regen_future",
                                scene_id=str(sc.id),
                                error=str(e)[:500],
                            )
                            g = "fail"
                        if g == "stop":
                            return None
                        if g == "fail":
                            failed_set.add(sc.id)
                            failed_ids_p.append(sc.id)
                db.expire_all()

        for sc in target_scenes:
            if sc.id in failed_set:
                continue
            _auto_pipeline_approve_scene_image(db, sc)
            if _scene_succeeded_image_count(db, sc.id) < min_scene_images:
                failed_ids_p.append(sc.id)
            db.commit()
        return failed_ids_p

    run_tail_images = pipeline_oversight_svc.tail_should_run_with_force("auto_images", tr, fs) and auto_scene_images_pre
    tail_auto_images_runs = run_tail_images
    run_tail_videos_m = pipeline_oversight_svc.tail_should_run_with_force("auto_videos", tr, fs) and (
        auto_scene_videos_pre or force_regen_videos
    )
    if bool(getattr(settings, "agent_run_auto_scene_coverage_clips", False)) and (
        run_tail_images or run_tail_videos_m
    ):
        run_cov = db.get(AgentRun, agent_run_uuid)
        if run_cov:
            run_cov.current_step = "auto_scene_coverage"
            _wt()._append_event(run_cov, "auto_scene_coverage", "running")
        db.commit()
        cov_ok = _auto_scene_coverage_pass(
            db,
            settings,
            project_id=pid,
            tenant_id=tenant_id,
            all_scenes=all_scenes,
            agent_run_uuid=agent_run_uuid,
            automation_character_prefix=automation_tail_character_prefix,
        )
        if cov_ok is None:
            return False
        run_cov2 = db.get(AgentRun, agent_run_uuid)
        if run_cov2:
            _wt()._append_event(run_cov2, "auto_scene_coverage", "succeeded")
        db.commit()

    if tail_auto_images_runs:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            run.current_step = "auto_images"
            _wt()._append_event(
                run,
                "auto_images",
                "running",
                scene_total=len(all_scenes),
                min_stills_per_scene=min_scene_images,
                media_retry_passes_cap=_AUTO_SCENE_MEDIA_MAX_PASSES,
                auto_images_max_concurrency=auto_images_max_concurrency,
            )
        db.commit()
        if bool(getattr(settings, "agent_run_use_pexels_for_scenes", False)):
            pm = str(getattr(settings, "agent_run_pexels_scene_media_mode", "photos") or "photos").strip().lower()
            if pm not in ("photos", "videos", "both"):
                pm = "photos"
            if pm in ("photos", "both"):
                maybe_fill_pexels_for_project_scenes(db, settings, project)
        img_failed = _auto_image_pass(all_scenes)
        if img_failed is None:
            return False
        pass_num = 1
        while img_failed and pass_num < _AUTO_SCENE_MEDIA_MAX_PASSES:
            log.warning(
                "auto_pipeline_images_retry",
                project_id=str(pid),
                pass_num=pass_num + 1,
                failed_count=len(img_failed),
                scene_ids=[str(x) for x in img_failed[:48]],
            )
            run = db.get(AgentRun, agent_run_uuid)
            if run:
                _wt()._append_event(
                    run,
                    "auto_images",
                    "retry",
                    failed_scene_count=len(img_failed),
                    failed_scene_ids=[str(x) for x in img_failed[:64]],
                    pass_num=pass_num + 1,
                )
                db.commit()
            retry_scenes = [s for s in all_scenes if s.id in set(img_failed)]
            img_failed = _auto_image_pass(retry_scenes)
            if img_failed is None:
                return False
            pass_num += 1
        if img_failed:
            raise ValueError(
                "AUTO_IMAGE_FAILED_SCENES_AFTER_RETRY: "
                + ",".join(str(x) for x in img_failed[:32])
            )
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            _wt()._append_event(run, "auto_images", "succeeded")
            db.commit()
    else:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            skip_reason = (
                "auto_generate_scene_images_false"
                if not auto_scene_images_pre
                else "oversight_tail_resume"
            )
            _wt()._append_event(run, "auto_images", "skipped", reason=skip_reason)
        db.commit()

    auto_scene_videos = auto_scene_videos_pre
    run_tail_videos = pipeline_oversight_svc.tail_should_run_with_force("auto_videos", tr, fs) and (
        auto_scene_videos or force_regen_videos
    )
    if run_tail_videos:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            run.current_step = "auto_videos"
            _wt()._append_event(
                run,
                "auto_videos",
                "running",
                scene_total=len(all_scenes),
                min_clips_per_scene=min_scene_videos,
                media_retry_passes_cap=_AUTO_SCENE_MEDIA_MAX_PASSES,
            )
        db.commit()
        if bool(getattr(settings, "agent_run_use_pexels_for_scenes", False)):
            pm = str(getattr(settings, "agent_run_pexels_scene_media_mode", "photos") or "photos").strip().lower()
            if pm not in ("photos", "videos", "both"):
                pm = "photos"
            if pm == "videos" or (pm == "both" and not tail_auto_images_runs):
                maybe_fill_pexels_for_project_scenes(db, settings, project)
        had_video_at_start = {sc.id for sc in all_scenes if _scene_has_succeeded_video(db, sc.id)}

        def _auto_video_pass(target_scenes: list[Scene]) -> list[uuid.UUID] | None:
            failed_v: list[uuid.UUID] = []

            def _gen_one_video(sc: Scene) -> str:
                jv = _synthetic_job(
                    tenant_id=tenant_id,
                    project_id=pid,
                    jtype="scene_generate_video",
                    payload={
                        "scene_id": str(sc.id),
                        "tenant_id": tenant_id,
                        "generation_tier": "preview",
                        "agent_run_id": str(agent_run_uuid),
                    },
                )
                try:
                    vout = _phase3_video_generate(db, jv)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "auto_pipeline_video_scene_failed",
                        scene_id=str(sc.id),
                        error=str(e)[:800],
                    )
                    return "fail"
                if isinstance(vout, dict) and vout.get("stopped"):
                    return "stop"
                return "ok"

            for sc in target_scenes:
                if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                    return None
                scene_failed = False
                while _scene_succeeded_video_count(db, sc.id) < min_scene_videos:
                    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                        return None
                    g = _gen_one_video(sc)
                    if g == "stop":
                        return None
                    if g == "fail":
                        failed_v.append(sc.id)
                        scene_failed = True
                        break
                    db.commit()
                if scene_failed:
                    continue
                if force_regen_videos:
                    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
                        return None
                    g = _gen_one_video(sc)
                    if g == "stop":
                        return None
                    if g == "fail":
                        failed_v.append(sc.id)
                        db.commit()
                        continue
                    db.commit()
                if _scene_succeeded_video_count(db, sc.id) < min_scene_videos and sc.id not in failed_v:
                    failed_v.append(sc.id)
                db.commit()
            return failed_v

        vid_failed = _auto_video_pass(all_scenes)
        if vid_failed is None:
            return False
        vpass = 1
        while vid_failed and vpass < _AUTO_SCENE_MEDIA_MAX_PASSES:
            log.warning(
                "auto_pipeline_videos_retry",
                project_id=str(pid),
                pass_num=vpass + 1,
                failed_count=len(vid_failed),
            )
            run = db.get(AgentRun, agent_run_uuid)
            if run:
                _wt()._append_event(
                    run,
                    "auto_videos",
                    "retry",
                    failed_scene_count=len(vid_failed),
                    failed_scene_ids=[str(x) for x in vid_failed[:64]],
                    pass_num=vpass + 1,
                )
                db.commit()
            retry_v = [s for s in all_scenes if s.id in set(vid_failed)]
            vid_failed = _auto_video_pass(retry_v)
            if vid_failed is None:
                return False
            vpass += 1
        abort_on_vid = run_opts_pre.get("abort_on_auto_video_failure")
        if abort_on_vid is None:
            strict_video_fail = bool(getattr(settings, "agent_run_abort_on_auto_video_failure", False))
        else:
            strict_video_fail = bool(abort_on_vid)
        if vid_failed and strict_video_fail:
            raise ValueError(
                "AUTO_VIDEO_FAILED_SCENES_AFTER_RETRY: "
                + ",".join(str(x) for x in vid_failed[:32])
            )
        video_skipped = len(had_video_at_start)
        video_generated = sum(
            1 for sc in all_scenes if _scene_has_succeeded_video(db, sc.id) and sc.id not in had_video_at_start
        )
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            if vid_failed:
                log.warning(
                    "auto_pipeline_videos_incomplete_continuing",
                    project_id=str(pid),
                    failed_count=len(vid_failed),
                    failed_scene_ids=[str(x) for x in vid_failed[:32]],
                )
                _wt()._append_event(
                    run,
                    "auto_videos",
                    "partial_failed",
                    generated=video_generated,
                    skipped_existing=video_skipped,
                    failed_scene_count=len(vid_failed),
                    failed_scene_ids=[str(x) for x in vid_failed[:64]],
                    note=(
                        "Some scenes still lack enough succeeded video assets after retries; continuing to timeline and export. "
                        "Re-generate failed clips in Studio, or set agent_run_abort_on_auto_video_failure (or pipeline_options.abort_on_auto_video_failure) to stop the run on this condition."
                    ),
                )
            else:
                _wt()._append_event(
                    run,
                    "auto_videos",
                    "succeeded",
                    generated=video_generated,
                    skipped_existing=video_skipped,
                )
            db.commit()
    else:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            skip_reason = (
                "oversight_tail_resume"
                if auto_scene_videos
                else "auto_generate_scene_videos_false"
            )
            run.current_step = "auto_videos"
            _wt()._append_event(run, "auto_videos", "skipped", reason=skip_reason)
        db.commit()

    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
        return False
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        run.current_step = "auto_timeline"
        _wt()._append_event(run, "auto_timeline", "running")
    db.commit()
    clips: list[dict[str, Any]] = []
    clip_order = 0
    proj_for_timeline = db.get(Project, pid)
    use_all_approved = bool(proj_for_timeline and getattr(proj_for_timeline, "use_all_approved_scene_media", False)) or (
        bool(getattr(settings, "agent_run_auto_scene_coverage_clips", False))
        and pipeline_oversight_svc.tail_should_run_with_force("auto_timeline", tr, fs)
    )
    storage_root_tl = Path(settings.local_storage_root).resolve()
    ffprobe_bin_tl = (getattr(settings, "ffprobe_bin", None) or "ffprobe").strip() or "ffprobe"
    timeout_tl = float(settings.ffmpeg_timeout_sec)
    for sc in all_scenes:
        if use_all_approved:
            use_rows = list_export_ready_scene_visuals_ordered(
                db,
                scene_id=sc.id,
                project_id=pid,
                tenant_id=tenant_id,
                storage_root=storage_root_tl,
                allow_unapproved_media=allow_unapproved_media,
            )
            if use_rows:
                n_img = sum(1 for a in use_rows if str(a.asset_type or "").lower() == "image")
                scene_dur = effective_scene_visual_budget_sec(
                    db,
                    scene=sc,
                    project_id=pid,
                    base_clip_sec=_wt()._scene_clip_duration_sec(settings),
                    storage_root=storage_root_tl,
                    ffprobe_bin=ffprobe_bin_tl,
                    timeout_sec=timeout_tl,
                    tail_padding_sec=_wt()._scene_vo_tail_padding_sec(settings),
                )
                per_img = scene_dur / max(n_img, 1) if n_img else scene_dur
                for a in use_rows:
                    at = str(a.asset_type or "").lower()
                    if at == "video":
                        clips.append(
                            {
                                "order_index": clip_order,
                                "source": {"kind": "asset", "asset_id": str(a.id)},
                            }
                        )
                    else:
                        d = per_img if n_img > 1 else scene_dur
                        clips.append(
                            {
                                "order_index": clip_order,
                                "source": {"kind": "asset", "asset_id": str(a.id)},
                                "duration_sec": max(0.25, d),
                            }
                        )
                    clip_order += 1
                continue
        vids = list(
            db.scalars(
                select(Asset)
                .where(
                    Asset.scene_id == sc.id,
                    Asset.asset_type == "video",
                    Asset.status == "succeeded",
                )
                .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
            ).all()
        )
        approved_vids = [a for a in vids if a.approved_at is not None]
        use_vids = approved_vids if approved_vids else vids
        if use_vids:
            for vid in use_vids:
                if vid.approved_at is None:
                    vid.approved_at = datetime.now(timezone.utc)
            scene_dur_vid = effective_scene_visual_budget_sec(
                db,
                scene=sc,
                project_id=pid,
                base_clip_sec=_wt()._scene_clip_duration_sec(settings),
                storage_root=storage_root_tl,
                ffprobe_bin=ffprobe_bin_tl,
                timeout_sec=timeout_tl,
                tail_padding_sec=_wt()._scene_vo_tail_padding_sec(settings),
            )
            if len(use_vids) == 1:
                clips.append(
                    {
                        "order_index": clip_order,
                        "source": {"kind": "asset", "asset_id": str(use_vids[0].id)},
                        "duration_sec": scene_dur_vid,
                    }
                )
                clip_order += 1
            else:
                for vid in use_vids:
                    clips.append(
                        {
                            "order_index": clip_order,
                            "source": {"kind": "asset", "asset_id": str(vid.id)},
                            # No duration_sec: rough/final export uses each file's length (ffprobe).
                        }
                    )
                    clip_order += 1
            continue
        imgs = list(
            db.scalars(
                select(Asset)
                .where(
                    Asset.scene_id == sc.id,
                    Asset.asset_type == "image",
                    Asset.status == "succeeded",
                )
                .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
            ).all()
        )
        approved_only = [a for a in imgs if a.approved_at is not None]
        use_imgs = approved_only if approved_only else imgs
        if not use_imgs:
            # Budget smoke: placeholder stills + optional local_ffmpeg (no cloud video). Tail resume can
            # skip auto_images while a scene still has no still; local_ffmpeg needs a source image anyway.
            proj_tl = proj_for_timeline or db.get(Project, pid)
            want_placeholder_heal = bool(
                proj_tl
                and str(getattr(proj_tl, "preferred_image_provider", "") or "").strip().lower() == "placeholder"
            ) or bool(getattr(settings, "director_placeholder_media", False))
            if want_placeholder_heal:
                j_heal = _synthetic_job(
                    tenant_id=tenant_id,
                    project_id=pid,
                    jtype="scene_generate_image",
                    payload={
                        "scene_id": str(sc.id),
                        "tenant_id": tenant_id,
                        "generation_tier": "preview",
                        "agent_run_id": str(agent_run_uuid),
                        "_automation_character_prefix": automation_tail_character_prefix,
                    },
                )
                try:
                    heal_out = _phase3_image_generate(db, j_heal)
                    if isinstance(heal_out, dict) and heal_out.get("ok") is True:
                        _auto_pipeline_approve_scene_image(db, sc)
                    db.commit()
                    imgs = list(
                        db.scalars(
                            select(Asset)
                            .where(
                                Asset.scene_id == sc.id,
                                Asset.asset_type == "image",
                                Asset.status == "succeeded",
                            )
                            .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
                        ).all()
                    )
                    approved_only = [a for a in imgs if a.approved_at is not None]
                    use_imgs = approved_only if approved_only else imgs
                except Exception as exc:
                    log.warning(
                        "auto_timeline_placeholder_heal_failed",
                        scene_id=str(sc.id),
                        error=str(exc)[:500],
                    )
            if not use_imgs:
                raise ValueError(f"AUTO_TIMELINE_MISSING_IMAGE_{sc.id}")
        scene_dur = effective_scene_visual_budget_sec(
            db,
            scene=sc,
            project_id=pid,
            base_clip_sec=_wt()._scene_clip_duration_sec(settings),
            storage_root=storage_root_tl,
            ffprobe_bin=ffprobe_bin_tl,
            timeout_sec=timeout_tl,
            tail_padding_sec=_wt()._scene_vo_tail_padding_sec(settings),
        )
        if len(use_imgs) == 1:
            clips.append(
                {
                    "order_index": clip_order,
                    "source": {"kind": "asset", "asset_id": str(use_imgs[0].id)},
                    "duration_sec": scene_dur,
                }
            )
            clip_order += 1
        else:
            per = scene_dur / float(len(use_imgs))
            for img in use_imgs:
                clips.append(
                    {
                        "order_index": clip_order,
                        "source": {"kind": "asset", "asset_id": str(img.id)},
                        "duration_sec": max(0.25, per),
                    }
                )
                clip_order += 1
    tj: dict[str, Any] = {
        "schema_version": 2,
        "clips": clips,
        "music_bed_id": None,
    }
    validate_timeline_document(tj)
    tv = TimelineVersion(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=pid,
        version_name="Auto pipeline",
        timeline_json=tj,
        render_status="draft",
        output_url=None,
    )
    db.add(tv)
    db.flush()
    tv_id = tv.id
    storage_root_pre = Path(settings.local_storage_root).resolve()
    proj_auto = db.get(Project, pid)
    if proj_auto:
        _phase5_auto_heal_before_export(
            db,
            project=proj_auto,
            tv=tv,
            storage_root=storage_root_pre,
            allow_unapproved_media=allow_unapproved_media,
        )
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        _wt()._append_event(run, "auto_timeline", "succeeded", timeline_version_id=str(tv_id))
        db.commit()

    readiness = compute_phase5_readiness(
        db,
        project_id=pid,
        tenant_id=tenant_id,
        timeline_version_id=tv_id,
        storage_root=storage_root_pre,
        export_stage="rough_cut",
        allow_unapproved_media=allow_unapproved_media,
    )
    if not readiness.get("ready"):
        raise_phase5_gate(readiness, label="AUTO_ROUGH_NOT_READY")

    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
        return False
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        run.current_step = "auto_rough_cut"
        _wt()._append_event(run, "auto_rough_cut", "running")
    db.commit()
    rj = _synthetic_job(
        tenant_id=tenant_id,
        project_id=pid,
        jtype="rough_cut",
        payload={
            "timeline_version_id": str(tv_id),
            "project_id": str(pid),
            "tenant_id": tenant_id,
            "allow_unapproved_media": allow_unapproved_media,
        },
    )
    _wt()._rough_cut(db, rj, settings)
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        _wt()._append_event(run, "auto_rough_cut", "succeeded")
        db.commit()

    if _wt()._agent_run_checkpoint(db, agent_run_uuid) == "stop":
        return False
    db.refresh(tv)
    _wt()._attach_latest_music_bed_if_missing(
        db,
        tv,
        tenant_id=tenant_id,
        project_id=pid,
        storage_root=storage_root_pre,
        director_auth_enabled=bool(getattr(settings, "director_auth_enabled", True)),
    )
    db.refresh(tv)
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        run.current_step = "auto_final_cut"
        _wt()._append_event(run, "auto_final_cut", "running")
    db.commit()
    fj = _synthetic_job(
        tenant_id=tenant_id,
        project_id=pid,
        jtype="final_cut",
        payload={
            "timeline_version_id": str(tv_id),
            "project_id": str(pid),
            "tenant_id": tenant_id,
            "allow_unapproved_media": allow_unapproved_media,
            "burn_subtitles_into_video": bool(getattr(settings, "burn_subtitles_in_final_cut_default", False)),
        },
    )
    _wt()._final_cut(db, fj, settings)
    project = db.get(Project, pid)
    if project:
        project.workflow_phase = "final_video_ready"
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        _wt()._append_event(run, "auto_final_cut", "succeeded", timeline_version_id=str(tv_id))
        db.commit()
    log.info(
        "agent_full_video_tail_timing",
        phase="complete",
        agent_run_id=str(agent_run_uuid),
        project_id=str(pid),
        elapsed_sec=round(time.perf_counter() - tail_wall_t0, 3),
        scene_count=len(all_scenes),
    )
    return True


def _project_has_story_research_review_report(db, project_id: uuid.UUID) -> bool:
    """True if the project already has a story-vs-research critic row (run at most once)."""
    n = db.scalar(
        select(func.count())
        .select_from(CriticReport)
        .where(
            CriticReport.project_id == project_id,
            CriticReport.target_type == "project",
            CriticReport.target_id == project_id,
            CriticReport.meta_json.isnot(None),
            CriticReport.meta_json["kind"].astext == "story_research_review",
        )
    )
    return int(n or 0) > 0



def _run_agent_run_impl(agent_run_id: str) -> None:
    aid = uuid.UUID(agent_run_id)
    with SessionLocal() as db:
        run = db.get(AgentRun, aid)
        if not run:
            log.error("agent_run_not_found", agent_run_id=agent_run_id)
            return
        settings = _wt()._worker_runtime_for_agent_run(db, run)
        if run.status in ("cancelled", "succeeded", "failed", "blocked"):
            log.info("agent_run_skip_terminal", agent_run_id=agent_run_id, status=run.status)
            return
        agent_run_just_started = False
        if _wt()._pipeline_control_dict(run.pipeline_control_json)["stop_requested"]:
            run.status = "cancelled"
            run.error_message = "Stopped by user"
            run.completed_at = datetime.now(timezone.utc)
            _wt()._append_event(run, "pipeline", "cancelled", reason="user_stop_before_start")
            db.commit()
            return
        # Only the first dequeue from "queued" should emit director/running — re-entrant polls after
        # pause yield must not reset step or duplicate the start event.
        if run.status == "queued":
            run.status = "running"
            if run.started_at is None:
                run.started_at = datetime.now(timezone.utc)
            run.current_step = "director"
            run.block_code = None
            run.block_message = None
            run.block_detail_json = None
            run.error_message = None
            _wt()._append_event(run, "director", "running")
            db.commit()
            agent_run_just_started = True

        def halt() -> bool:
            return _wt()._agent_run_checkpoint(db, aid) == "stop"

        project = db.get(Project, run.project_id)
        if agent_run_just_started and project:
            try:
                from director_api.services.telegram_notify import telegram_notify_run_started

                telegram_notify_run_started(settings, project.title, agent_run_id)
            except Exception as exc:
                log.warning("telegram_notify_run_started_failed", agent_run_id=agent_run_id, error=str(exc))
        if not project:
            run = db.get(AgentRun, aid)
            if run:
                run.status = "failed"
                run.error_message = "project not found"
                run.completed_at = datetime.now(timezone.utc)
                _wt()._append_event(run, "director", "failed", error_code="PROJECT_MISSING")
                db.commit()
            return

        if project.tenant_id != run.tenant_id:
            run = db.get(AgentRun, aid)
            if run:
                run.status = "failed"
                run.error_message = "agent run tenant does not match project"
                run.completed_at = datetime.now(timezone.utc)
                _wt()._append_event(run, "director", "failed", error_code="TENANT_MISMATCH")
                db.commit()
            return
        _uid = str(run.started_by_user_id) if getattr(run, "started_by_user_id", None) else None
        with llm_prompt_map_scope(build_resolved_prompt_map(db, run.tenant_id, _uid)):

                run_project_id = project.id
                opts_raw = getattr(run, "pipeline_options_json", None)
                cont, through, unattended = agent_resume_svc.parse_pipeline_options(opts_raw)
                force_steps = pipeline_oversight_svc.parse_force_pipeline_steps(opts_raw)
                _opts_d = opts_raw if isinstance(opts_raw, dict) else None
                _rw = _opts_d.get("rerun_web_research") if _opts_d else None
                rerun_web_research: bool | None = None if _rw is None else bool(_rw)
                force_replan_scenes = bool(isinstance(opts_raw, dict) and opts_raw.get("force_replan_scenes"))
                if "scenes" in force_steps:
                    force_replan_scenes = True

                oversight_earliest: str | None = None
                tail_resume: str | None = None
                _root_storage = Path(settings.local_storage_root).resolve()
                if halt():
                    return
                if (
                    cont
                    and through in ("full_video", "critique")
                    and bool(getattr(settings, "agent_oversight_llm_enabled", True))
                    and not bool(getattr(settings, "agent_run_fast", False))
                ):
                    det_gap = pipeline_oversight_svc.earliest_gap_deterministic(
                        db, project, _root_storage if _root_storage.is_dir() else None
                    )
                    usage_ov: list[dict[str, Any]] = []
                    llm_gap: str | None = None
                    gaps_out: list[dict[str, Any]] = []
                    rationale = ""
                    if _wt()._active_text_llm_configured(settings):
                        try:
                            snap = pipeline_oversight_svc.build_oversight_snapshot(
                                db, project, _root_storage if _root_storage.is_dir() else None
                            )
                            llm_gap, gaps_out, rationale = pipeline_oversight_svc.oversight_llm_advisory(
                                snap, settings=settings, usage_sink=usage_ov
                            )
                            _wt()._flush_llm_usage(db, project.tenant_id, project.id, None, None, usage_ov)
                        except Exception as e:  # noqa: BLE001
                            log.warning("oversight_llm_failed", error=str(e)[:500])
                    oversight_earliest = pipeline_oversight_svc.merge_earliest_steps(det_gap, llm_gap)
                    tail_resume = pipeline_oversight_svc.tail_resume_from_oversight(oversight_earliest)
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._append_event(
                            run,
                            "oversight",
                            "succeeded",
                            earliest_gap=oversight_earliest,
                            deterministic_gap=det_gap,
                            llm_gap=llm_gap,
                            tail_resume=tail_resume,
                            gaps=gaps_out[:6],
                            rationale=sanitize_jsonb_text(rationale, 2000),
                        )
                        db.commit()
                if halt():
                    return

                rerun_from = (
                    pipeline_oversight_svc._canonical_step(str(opts_raw.get("rerun_from_step") or ""))
                    if isinstance(opts_raw, dict)
                    else None
                )
                pipeline_floor: str | None = None
                if rerun_from and rerun_from in pipeline_oversight_svc.OVERSIGHT_STEP_RANK:
                    pipeline_floor = rerun_from
                elif force_steps:
                    ranked_f = [
                        (pipeline_oversight_svc.OVERSIGHT_STEP_RANK[s], s)
                        for s in force_steps
                        if s in pipeline_oversight_svc.OVERSIGHT_STEP_RANK
                    ]
                    if ranked_f:
                        pipeline_floor = min(ranked_f)[1]
                if pipeline_floor:
                    oversight_earliest = pipeline_oversight_svc.clamp_oversight_floor(
                        oversight_earliest, pipeline_floor
                    )

                if isinstance(opts_raw, dict) and "auto_generate_scene_videos" in opts_raw:
                    auto_sv_pipeline = bool(opts_raw.get("auto_generate_scene_videos"))
                else:
                    auto_sv_pipeline = bool(getattr(settings, "agent_run_auto_generate_scene_videos", False))
                if isinstance(opts_raw, dict) and "auto_generate_scene_images" in opts_raw:
                    auto_si_pipeline = bool(opts_raw.get("auto_generate_scene_images"))
                else:
                    auto_si_pipeline = bool(getattr(settings, "agent_run_auto_generate_scene_images", True))

                if rerun_from and rerun_from in pipeline_oversight_svc.OVERSIGHT_STEP_RANK:
                    cont = True
                    if rerun_from in pipeline_oversight_svc.TAIL_STEPS:
                        tail_resume = pipeline_oversight_svc.normalize_tail_resume(
                            rerun_from,
                            auto_scene_videos=auto_sv_pipeline,
                            auto_scene_images=auto_si_pipeline,
                        )
                    else:
                        tail_resume = pipeline_oversight_svc.tail_resume_from_oversight(oversight_earliest)
                    if rerun_from == "scenes":
                        force_replan_scenes = True
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._append_event(run, "rerun", "requested", from_step=rerun_from)
                        db.commit()
                elif pipeline_floor and pipeline_floor in pipeline_oversight_svc.TAIL_STEPS:
                    tail_resume = pipeline_oversight_svc.normalize_tail_resume(
                        pipeline_floor,
                        auto_scene_videos=auto_sv_pipeline,
                        auto_scene_images=auto_si_pipeline,
                    )

                # Outline can use deterministic `chapter_outline_from_director` without API keys; chapter
                # scripts always need the configured text provider. Fail here so Automate / hands-off does
                # not spend phases then stall with workflow_phase stuck at outline_ready.
                if through in ("chapters", "critique", "full_video"):
                    proj_chk = db.get(Project, run_project_id)
                    if proj_chk:
                        would_skip_chapters = agent_resume_svc.should_skip_chapters(cont, proj_chk, db)
                        if not pipeline_oversight_svc.effective_resume_skip_with_force(
                            cont,
                            oversight_earliest,
                            "chapters",
                            would_skip_chapters,
                            force_steps,
                        ):
                            try:
                                _wt()._require_active_text_llm(settings, for_what="chapter script generation")
                            except ValueError as e:
                                run = db.get(AgentRun, aid)
                                if run:
                                    _wt()._agent_run_mark_failed(db, run, "pipeline", e)
                                log.warning(
                                    "agent_run_chapters_preflight_no_text_provider",
                                    agent_run_id=agent_run_id,
                                )
                                return

                if halt():
                    return

                if pipeline_oversight_svc.effective_resume_skip_with_force(
                    cont,
                    oversight_earliest,
                    "director",
                    agent_resume_svc.should_skip_director(cont, project),
                    force_steps,
                ):
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._append_event(run, "director", "skipped", reason="director_pack_present")
                        run.current_step = "research"
                        db.commit()
                else:
                    try:
                        if halt():
                            return
                        _wt()._ensure_director_pack(db, project, settings)
                        db.commit()
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._append_event(run, "director", "succeeded")
                            run.current_step = "research"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._agent_run_mark_failed(db, run, "director", e)
                        log.exception("agent_run_director_failed", agent_run_id=agent_run_id)
                        return

                if halt():
                    return

                would_skip_research = agent_resume_svc.should_skip_research(cont, project, db)
                research_forced = "research" in force_steps
                if rerun_web_research is False and not research_forced:
                    if agent_resume_svc.latest_dossier(db, project.id):
                        would_skip_research = True
                elif rerun_web_research is True:
                    would_skip_research = False

                user_declined_research_rerun = bool(
                    rerun_web_research is False
                    and not research_forced
                    and would_skip_research
                    and agent_resume_svc.latest_dossier(db, project.id)
                )
                if user_declined_research_rerun:
                    skipped_research = True
                else:
                    skipped_research = pipeline_oversight_svc.effective_resume_skip_with_force(
                        cont,
                        oversight_earliest,
                        "research",
                        would_skip_research,
                        force_steps,
                    )
                if skipped_research:
                    run = db.get(AgentRun, aid)
                    if run:
                        _skip_reason = (
                            "user_declined_rerun"
                            if rerun_web_research is False and not research_forced
                            else "existing_dossier_phase"
                        )
                        _wt()._append_event(run, "research", "skipped", reason=_skip_reason)
                        run.current_step = "outline"
                        db.commit()
                else:
                    try:
                        run = db.get(AgentRun, aid)
                        if not run:
                            log.error("agent_run_missing", agent_run_id=agent_run_id)
                            return
                        if halt():
                            return
                        _wt()._append_event(run, "research", "running")
                        db.commit()
                        project = db.get(Project, run_project_id)
                        if not project:
                            raise RuntimeError("project missing after director step")
                        _wt()._phase2_research_core(db, project, settings, agent_run_id=aid)
                        db.commit()
                        project = db.get(Project, run_project_id)
                        dossier = _wt()._latest_dossier(db, project.id) if project else None
                        if not dossier:
                            raise RuntimeError("research step did not create dossier")
                        _wt()._strict_research_gate(db, project, dossier, unattended=unattended)
                    except AgentRunStopRequested:
                        return
                    except AgentRunBlocked as e:
                        run = db.get(AgentRun, aid)
                        if run:
                            run.status = "blocked"
                            run.current_step = None
                            run.block_code = e.code
                            run.block_message = e.message[:8000]
                            run.block_detail_json = e.detail
                            run.completed_at = datetime.now(timezone.utc)
                            _wt()._append_event(
                                run,
                                "research_gate",
                                "blocked",
                                error_code=e.code,
                                message=e.message[:500],
                            )
                            db.commit()
                        log.warning("agent_run_blocked", agent_run_id=agent_run_id, code=e.code)
                        return
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._agent_run_mark_failed(db, run, "research", e)
                        log.exception("agent_run_research_failed", agent_run_id=agent_run_id)
                        return

                if halt():
                    return

                needs_research_approval_after_skip = False
                if skipped_research:
                    _proj_skip = db.get(Project, run_project_id)
                    if _proj_skip and agent_resume_svc.workflow_phase_rank(_proj_skip.workflow_phase) < 3:
                        _d_skip = agent_resume_svc.latest_dossier(db, _proj_skip.id)
                        if _d_skip:
                            try:
                                if halt():
                                    return
                                _wt()._strict_research_gate(db, _proj_skip, _d_skip, unattended=unattended)
                                needs_research_approval_after_skip = True
                            except AgentRunBlocked as e:
                                run = db.get(AgentRun, aid)
                                if run:
                                    run.status = "blocked"
                                    run.current_step = None
                                    run.block_code = e.code
                                    run.block_message = e.message[:8000]
                                    run.block_detail_json = e.detail
                                    run.completed_at = datetime.now(timezone.utc)
                                    _wt()._append_event(
                                        run,
                                        "research_gate",
                                        "blocked",
                                        error_code=e.code,
                                        message=e.message[:500],
                                    )
                                    db.commit()
                                log.warning("agent_run_blocked", agent_run_id=agent_run_id, code=e.code)
                                return

                if not skipped_research or needs_research_approval_after_skip:
                    try:
                        project = db.get(Project, run_project_id)
                        if not project:
                            raise RuntimeError("project missing before research approval")
                        dossier = _wt()._latest_dossier(db, project.id)
                        if dossier:
                            dossier.status = "approved"
                            dossier.approved_at = datetime.now(timezone.utc)
                            dossier.approved_notes = "Auto-approved after strict research gate (agent run)"
                        project.workflow_phase = "research_approved"
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._append_event(run, "research", "succeeded", dossier_id=str(dossier.id) if dossier else None)
                            run.current_step = "outline"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._agent_run_mark_failed(db, run, "research_approve", e)
                        log.exception("agent_run_approve_failed", agent_run_id=agent_run_id)
                        return

                if halt():
                    return

                if pipeline_oversight_svc.effective_resume_skip_with_force(
                    cont,
                    oversight_earliest,
                    "outline",
                    agent_resume_svc.should_skip_outline(cont, project),
                    force_steps,
                ):
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._append_event(run, "outline", "skipped", reason="outline_already_done")
                        run.current_step = "chapters"
                        db.commit()
                else:
                    try:
                        run = db.get(AgentRun, aid)
                        if not run:
                            log.error("agent_run_missing", agent_run_id=agent_run_id)
                            return
                        if halt():
                            return
                        _wt()._append_event(run, "outline", "running")
                        db.commit()
                        project = db.get(Project, run_project_id)
                        if not project:
                            raise RuntimeError("project missing before outline")
                        _wt()._phase2_outline_core(db, project, settings)
                        db.commit()
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._append_event(run, "outline", "succeeded")
                            run.current_step = "chapters"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._agent_run_mark_failed(db, run, "outline", e)
                        log.exception("agent_run_outline_failed", agent_run_id=agent_run_id)
                        return

                if halt():
                    return

                if pipeline_oversight_svc.effective_resume_skip_with_force(
                    cont,
                    oversight_earliest,
                    "chapters",
                    agent_resume_svc.should_skip_chapters(cont, project, db),
                    force_steps,
                ):
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._append_event(run, "chapters", "skipped", reason="scripts_already_done")
                        run.current_step = "scenes"
                        db.commit()
                else:
                    try:
                        run = db.get(AgentRun, aid)
                        if not run:
                            log.error("agent_run_missing", agent_run_id=agent_run_id)
                            return
                        if halt():
                            return
                        _wt()._append_event(run, "chapters", "running")
                        db.commit()
                        project = db.get(Project, run_project_id)
                        if not project:
                            raise RuntimeError("project missing before chapters")
                        _wt()._phase2_chapters_core(db, project, settings, preserve_substantive_scripts=cont)
                        db.commit()
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._append_event(run, "chapters", "succeeded")
                            run.current_step = "scenes"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._agent_run_mark_failed(db, run, "chapters", e)
                        log.exception("agent_run_chapters_failed", agent_run_id=agent_run_id)
                        return

                if through == "chapters":
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._append_event(
                            run,
                            "pipeline",
                            "succeeded",
                            stopped_after="chapters",
                            message=(
                                "Done — chapter scripts are ready. Use the editor for scene planning and media, "
                                "or switch to Auto and use Continue pipeline to run critics and beyond."
                            ),
                        )
                        run.status = "succeeded"
                        run.current_step = None
                        run.completed_at = datetime.now(timezone.utc)
                        db.commit()
                    log.info("agent_run_succeeded_chapters_only", agent_run_id=agent_run_id, project_id=str(run_project_id))
                    return

                if halt():
                    return

                project = db.get(Project, run_project_id)
                if project and pipeline_oversight_svc.effective_resume_skip_with_force(
                    cont,
                    oversight_earliest,
                    "scenes",
                    agent_resume_svc.should_skip_scenes_plan(
                        cont,
                        project,
                        db,
                        through=through,
                        force_replan_scenes=force_replan_scenes,
                    ),
                    force_steps,
                ):
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._append_event(run, "scenes", "skipped", reason="scenes_already_planned")
                        run.current_step = "story_research_review"
                        db.commit()
                else:
                    try:
                        run = db.get(AgentRun, aid)
                        if not run:
                            log.error("agent_run_missing", agent_run_id=agent_run_id)
                            return
                        if halt():
                            return
                        _wt()._append_event(run, "scenes", "running")
                        db.commit()
                        project = db.get(Project, run_project_id)
                        if not project:
                            raise RuntimeError("project missing before scenes")
                        chapters_list = list(
                            db.scalars(
                                select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)
                            ).all()
                        )
                        planned = 0
                        skipped_short_script = 0
                        chapters_skipped_existing_scenes = 0
                        plan_queue: list[Chapter] = []
                        oversight_fs = pipeline_oversight_svc.oversight_blocks_resume_skip(oversight_earliest, "scenes")
                        for ch in chapters_list:
                            if halt():
                                return
                            n_existing = (
                                db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch.id)) or 0
                            )
                            act = agent_resume_svc.agent_scenes_chapter_planning_action(
                                ch.script_text,
                                cont,
                                force_replan_scenes,
                                int(n_existing),
                                through=through,
                                oversight_force_scenes=oversight_fs,
                            )
                            if act == "short_script":
                                skipped_short_script += 1
                                continue
                            if act == "skip_existing_scenes":
                                chapters_skipped_existing_scenes += 1
                                continue
                            plan_queue.append(ch)

                        if plan_queue:
                            scene_plan_char_prefix = character_consistency_prefix(db, project.id, max_chars=2000)
                            scene_plan_char_bible = character_bible_for_llm_context(db, project.id, max_chars=6000)
                            for plan_i, ch in enumerate(plan_queue):
                                if halt():
                                    return
                                run = db.get(AgentRun, aid)
                                if run:
                                    _wt()._append_event(
                                        run,
                                        "scenes",
                                        "progress",
                                        chapter_index=int(plan_i + 1),
                                        chapters_total=int(len(plan_queue)),
                                        chapter_title=sanitize_jsonb_text(str(ch.title or ""), 240),
                                    )
                                    db.commit()
                                log.info(
                                    "agent_run_scenes_chapter_start",
                                    agent_run_id=str(aid),
                                    project_id=str(project.id),
                                    chapter_id=str(ch.id),
                                    chapter_plan_index=int(plan_i + 1),
                                    chapters_to_plan=int(len(plan_queue)),
                                )
                                _phase3_scenes_plan_for_chapter(
                                    db,
                                    ch,
                                    project,
                                    settings,
                                    cached_character_consistency_prefix=scene_plan_char_prefix,
                                    cached_character_bible_for_llm=scene_plan_char_bible,
                                )
                                planned += 1
                        db.commit()
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._append_event(
                                run,
                                "scenes",
                                "succeeded",
                                chapters_planned=planned,
                                chapters_skipped_short_script=skipped_short_script,
                                chapters_skipped_existing_scenes=chapters_skipped_existing_scenes,
                            )
                            run.current_step = "story_research_review"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._agent_run_mark_failed(db, run, "scenes", e)
                        log.exception("agent_run_scenes_failed", agent_run_id=agent_run_id)
                        return

                if halt():
                    return

                project = db.get(Project, run_project_id)
                # Story vs research: one automatic LLM pass per project after scenes, then never again (critic row is the latch).
                if project and _project_has_story_research_review_report(db, project.id):
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._append_event(
                            run,
                            "story_research_review",
                            "skipped",
                            reason="already_completed",
                        )
                        db.commit()
                    project = db.get(Project, run_project_id)
                    run = db.get(AgentRun, aid)
                    if through == "full_video" and project and run:
                        try:
                            if not _run_agent_full_pipeline_tail(
                                db, run, aid, project, settings, tail_resume_from=tail_resume, force_steps=force_steps
                            ):
                                return
                        except Exception as e:  # noqa: BLE001
                            _wt()._agent_run_mark_failed(db, run, "full_video", e)
                            log.exception("agent_run_full_video_failed", agent_run_id=agent_run_id)
                            return
                    run = db.get(AgentRun, aid)
                    if run:
                        if run.status == "cancelled":
                            return
                        run.status = "succeeded"
                        run.current_step = None
                        run.completed_at = datetime.now(timezone.utc)
                        db.commit()
                    log.info("agent_run_succeeded", agent_run_id=agent_run_id, project_id=str(run_project_id))
                    return

                try:
                    run = db.get(AgentRun, aid)
                    if not run:
                        log.error("agent_run_missing", agent_run_id=agent_run_id)
                        return
                    if halt():
                        return
                    _wt()._append_event(run, "story_research_review", "running")
                    db.commit()
                    project = db.get(Project, run_project_id)
                    if not project:
                        raise RuntimeError("project missing before story_research_review")

                    agent_meta: dict[str, Any] = {"source": "agent_run"}
                    fast = bool(settings.agent_run_fast)
                    no_key = not _wt()._active_text_llm_configured(settings)

                    if fast or no_key:
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._append_event(
                                run,
                                "story_research_review",
                                "succeeded",
                                skipped_llm=True,
                                note="agent_run_fast" if fast else "missing_text_llm_credentials",
                            )
                        if project and not _project_has_story_research_review_report(db, project.id):
                            note = "agent_run_fast" if fast else "missing_text_llm_credentials"
                            db.add(
                                CriticReport(
                                    id=uuid.uuid4(),
                                    tenant_id=project.tenant_id,
                                    project_id=project.id,
                                    target_type="project",
                                    target_id=project.id,
                                    job_id=None,
                                    score=1.0,
                                    passed=True,
                                    dimensions_json={"aligned_with_research": True, "skipped_llm": True, "note": note},
                                    issues_json=[],
                                    recommendations_json=[],
                                    continuity_json=None,
                                    baseline_score=None,
                                    prior_report_id=None,
                                    meta_json={**agent_meta, "kind": "story_research_review"},
                                )
                            )
                            db.flush()
                    else:
                        llm_u_sr: list[dict[str, Any]] = []
                        dossier = agent_resume_svc.latest_dossier(db, project.id)
                        chapters_list_sr = list(
                            db.scalars(
                                select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)
                            ).all()
                        )
                        story_parts: list[dict[str, Any]] = []
                        for ch in chapters_list_sr:
                            st = (ch.script_text or "").strip()
                            if len(st) < 12:
                                continue
                            scenes_sr = list(
                                db.scalars(
                                    select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)
                                ).all()
                            )
                            narr_blocks = [
                                sanitize_jsonb_text(str(s.narration_text or ""), 2000) for s in scenes_sr
                            ]
                            story_parts.append(
                                {
                                    "chapter_title": ch.title,
                                    "order_index": ch.order_index,
                                    "script_excerpt": sanitize_jsonb_text(st, 8000),
                                    "scene_narration_excerpts": narr_blocks[:24],
                                }
                            )
                        dossier_blob = ""
                        if dossier is not None:
                            bj = dossier.body_json
                            if isinstance(bj, dict):
                                dossier_blob = json.dumps(bj, ensure_ascii=False)[:20000]
                            else:
                                dossier_blob = str(bj)[:20000]
                        pay = {
                            "project_topic": (project.topic or "")[:4000],
                            "research_dossier": dossier_blob,
                            "chapters": story_parts,
                        }
                        if halt():
                            return
                        parsed = phase4_llm.story_research_consistency_review(
                            pay, settings=settings, usage_sink=llm_u_sr
                        )
                        _wt()._flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u_sr)
                        if parsed is None:
                            parsed = {
                                "alignment_score": 0.5,
                                "aligned_with_research": True,
                                "summary": "Story vs research review did not return JSON (check API key / model).",
                                "issues": [],
                                "recommendations": [],
                            }
                        db.add(
                            CriticReport(
                                id=uuid.uuid4(),
                                tenant_id=project.tenant_id,
                                project_id=project.id,
                                target_type="project",
                                target_id=project.id,
                                job_id=None,
                                score=float(parsed["alignment_score"]),
                                passed=True,
                                dimensions_json={
                                    "aligned_with_research": parsed["aligned_with_research"],
                                    "alignment_score": parsed["alignment_score"],
                                },
                                issues_json=parsed.get("issues"),
                                recommendations_json=parsed.get("recommendations"),
                                continuity_json=None,
                                baseline_score=None,
                                prior_report_id=None,
                                meta_json={**agent_meta, "kind": "story_research_review"},
                            )
                        )
                        db.flush()
                        run = db.get(AgentRun, aid)
                        if run:
                            _wt()._append_event(
                                run,
                                "story_research_review",
                                "succeeded",
                                critic_report_written=True,
                                alignment_score=float(parsed["alignment_score"]),
                            )

                    project = db.get(Project, run_project_id)
                    if project:
                        project.workflow_phase = "critique_complete"
                    db.commit()
                    run = db.get(AgentRun, aid)
                    if run:
                        run.current_step = None
                        db.commit()
                    project = db.get(Project, run_project_id)
                    run = db.get(AgentRun, aid)
                    if through == "full_video" and project and run:
                        try:
                            if not _run_agent_full_pipeline_tail(
                                db, run, aid, project, settings, tail_resume_from=tail_resume, force_steps=force_steps
                            ):
                                return
                        except Exception as e:  # noqa: BLE001
                            _wt()._agent_run_mark_failed(db, run, "full_video", e)
                            log.exception("agent_run_full_video_failed", agent_run_id=agent_run_id)
                            return
                    run = db.get(AgentRun, aid)
                    if run:
                        if run.status == "cancelled":
                            return
                        run.status = "succeeded"
                        run.completed_at = datetime.now(timezone.utc)
                        db.commit()
                    log.info("agent_run_succeeded", agent_run_id=agent_run_id, project_id=str(run_project_id))
                except Exception as e:  # noqa: BLE001
                    run = db.get(AgentRun, aid)
                    if run:
                        _wt()._agent_run_mark_failed(db, run, "story_research_review", e)
                    log.exception("agent_run_story_research_review_failed", agent_run_id=agent_run_id)
                    return
