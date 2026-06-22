"""Celery tasks — run in worker process (imports DB + providers).

Split plan
----------
This file is intentionally being broken into per-phase modules.  The target
layout (in progress — move one section at a time to avoid import breakage):

  tasks/maintenance_tasks.py  — reap_stale_jobs  ✅ DONE
  tasks/phase2_impl.py        — _phase2_* + _characters_generate_core  ✅ DONE
  tasks/phase3_impl.py        — _phase3_image_generate / _phase3_video_generate / scene-plan  ✅ DONE
  tasks/phase4_impl.py        — _phase4_*critique* / _persist_revision_issues  ✅ DONE
  tasks/agent_run_control.py  — _agent_run_checkpoint / _append_event / exceptions  ✅ DONE
  tasks/phase5_impl.py        — phase5 auto-heal before export  ✅
  tasks/phase5_compile_impl.py — _rough_cut / _final_cut / _fine_cut / _export_bundle  ✅
  tasks/agent_tasks.py        — run_agent_run (Celery)  ✅
  tasks/smoke_tasks.py        — run_adapter_smoke_task  ✅

The ``run_*_job`` Celery task decorators remain here so task routing and beat
schedules stay in one place.  Do NOT add new top-level logic here — put it in
the appropriate target module instead.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from director_api.agents import phase2_llm, phase3_llm, phase4_llm
from director_api.agents.openai_client import openai_compatible_configured
from director_api.agents.parallel_openai_agents import (
    agents_sdk_import_ok,
    run_scene_critiques_parallel_sync,
)
from director_api.config import Settings, get_settings
from director_api.db.models import (
    AgentRun,
    Asset,
    Chapter,
    CriticReport,
    GenerationArtifact,
    Job,
    MusicBed,
    NarrationTrack,
    Project,
    ProjectCharacter,
    ResearchDossier,
    ResearchSource,
    Scene,
    TimelineVersion,
    UsageRecord,
)
from director_api.db.session import SessionLocal
from director_api.logging_config import configure_logging, get_logger
from director_api.providers import run_adapter_smoke
from director_api.providers.media_comfyui import generate_scene_image_comfyui, generate_scene_video_comfyui
from director_api.providers.media_fal import (
    fal_model_is_image_to_video,
    format_fal_result_message,
    generate_scene_image,
    generate_scene_video_fal,
)
from director_api.services.camera_perspective import inject_camera_perspective_into_prompt
from director_api.services.character_prompt import (
    character_bible_for_llm_context,
    character_consistency_prefix_for_scene,
    character_short_prefix_for_scene,
    load_project_character_bible_chunks,
    prompt_already_has_character_prefix,
)
from director_api.services import phase2 as phase2_svc
from director_api.services.usage_accounting import persist_llm_usage_entries
from director_api.services import phase3 as phase3_svc
from director_api.services import critic_policy as critic_policy_svc
from director_api.services import phase4 as phase4_svc
from director_api.services import agent_resume as agent_resume_svc
from director_api.services.erase_consent import (
    EraseConfirmationRequired,
    options_grant_erase_consent,
)
from director_api.services import pipeline_oversight as pipeline_oversight_svc
from director_api.services.phase5_readiness import (
    Phase5GateError,
    compute_phase5_readiness,
    raise_phase5_gate,
    get_timeline_asset_for_project,
)
from director_api.services.scene_coverage import coverage_visual_slots_needed, pick_coverage_payload
from director_api.services.clip_duration import clip_seconds_for_scene
from director_api.services.scene_timeline_duration import (
    effective_scene_visual_budget_sec,
    get_export_narration_budget_sec_for_scene,
    scene_vo_tail_padding_sec_from_settings,
)
from director_api.services.timeline_manifest_prefetch import manifest_prefetch_asset_hierarchy
from director_api.services.pexels_scene_fill import maybe_fill_pexels_for_project_scenes
from director_api.services.timeline_image_repair import list_export_ready_scene_visuals_ordered
from director_api.style_presets import (
    effective_narration_style,
    effective_video_visual_style,
    effective_visual_style,
)
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.services.job_worker_gate import acquire_job_for_work
from director_api.services.llm_prompt_runtime import llm_prompt_map_scope
from director_api.services.llm_prompt_service import build_resolved_prompt_map
from director_api.services.webhook_delivery import notify_job_terminal
from director_api.services.image_prompt_assembly import (
    character_consistency_block_for_image,
    polish_scene_image_prompt,
    polish_scene_video_prompt,
    scene_text_for_character_match,
    strip_redundant_visual_style_clauses,
)
from director_api.services.narration_bracket_visual import (
    base_image_prompt_from_scene_fields,
    append_video_character_dialogue_to_prompt,
    video_text_prompt_from_scene_fields,
)
from director_api.services.prompt_enhance import refine_bracket_visual_prompt_llm
from director_api.services.research_service import sanitize_jsonb_text
from director_api.storage.filesystem import FilesystemStorage
from director_api.tasks.celery_app import celery_app
from director_api.validation.phase2_schemas import (
    validate_director_pack,
)
from director_api.validation.phase3_schemas import validate_scene_plan_batch
from director_api.validation.timeline_schema import validate_timeline_document
from director_api.timeline_mix_levels import mix_music_volume_from_timeline, mix_narration_volume_from_timeline

from director_api.services.subtitles_vtt import assemble_project_subtitle_markdown, script_to_webvtt

from ffmpeg_pipelines.audio_concat import concat_audio_files
from ffmpeg_pipelines.audio_slot import normalize_audio_segment_to_duration
from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.probe import ffprobe_duration_seconds
from ffmpeg_pipelines.export_manifest import build_export_manifest
from ffmpeg_pipelines.mixed_timeline import compile_mixed_visual_timeline
from ffmpeg_pipelines.mux_master import mux_video_with_narration_and_music
from ffmpeg_pipelines.silence_audio import write_silence_aac
from ffmpeg_pipelines.overlay_video import burn_overlays_on_video
from ffmpeg_pipelines.paths import mkdir_parent, path_from_storage_url, path_is_readable_file, path_stat
from ffmpeg_pipelines.ffmpeg_tracked import ExportFfmpegRegistry
from ffmpeg_pipelines.slideshow import compile_image_slideshow
from ffmpeg_pipelines.still_to_video import encode_image_to_mp4


from director_api.tasks.agent_exceptions import (
    AgentRunBlocked,
    AgentRunPausedYield,
    AgentRunStopRequested,
)
from director_api.tasks.agent_run_control import (
    agent_run_checkpoint as _agent_run_checkpoint,
    append_event as _append_event,
    payload_agent_run_uuid as _payload_agent_run_uuid,
    pipeline_control_dict as _pipeline_control_dict,
)
from director_api.tasks.phase2_impl import (
    _characters_generate_core,
    _phase2_chapter_script_regenerate_core,
    _phase2_chapters_core,
    _phase2_outline_core,
    _phase2_research_core,
)
from director_api.tasks.phase4_impl import (
    _phase4_chapter_critique,
    _phase4_scene_critique,
    _phase4_scene_critique_core,
    _phase4_scene_critic_revision,
    _scene_critic_revision_apply_from_latest_report,
)
from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export
from director_api.tasks.worker_helpers import (
    _asset_running_guard,
    _make_agent_run_stop_signal,
    _make_job_stop_signal,
    _payload_stop_requested,
    _record_usage,
    _synthetic_job,
    _worker_runtime_for_agent_run,
    _worker_runtime_for_job,
)
from director_api.tasks.phase3_impl import (
    _phase3_image_generate,
    _phase3_scene_extend,
    _phase3_scene_still_job_succeeded,
    _phase3_scenes_generate,
    _phase3_scenes_plan_for_chapter,
    _phase3_video_generate,
)
from director_api.services import pipeline_fallback_events as pipeline_fallback_svc


# On Windows ``--pool=solo``, Celery's *hard* time limit can terminate the whole worker, not just the task.
# Must stay above ``Settings.ffmpeg_timeout_sec`` (single subprocess) and typical chained FFmpeg wall time.
_CELERY_PHASE3_SOFT_SEC = 7200
_CELERY_PHASE3_HARD_SEC = 8100
_CELERY_PHASE5_SOFT_SEC = 7200
_CELERY_PHASE5_HARD_SEC = 9000
_CELERY_AGENT_RUN_SOFT_SEC = 14_400
_CELERY_AGENT_RUN_HARD_SEC = 15_300



configure_logging()
log = get_logger(__name__)

from director_api.tasks.agent_tasks import run_agent_run
from director_api.tasks.smoke_tasks import run_adapter_smoke_task
from director_api.tasks.phase5_compile_impl import (
    _append_timeline_export_warnings,
    _attach_latest_music_bed_if_missing,
    _bind_asset_local_file,
    _build_scene_timeline_narration_stem,
    _build_timeline_export_manifest,
    _count_scene_narration_tracks,
    _expand_manifest_and_slots_for_full_narration,
    _export_bundle,
    _export_chapter_title_card_sec,
    _final_cut,
    _final_cut_audio_slots_from_manifest,
    _fine_cut,
    _latest_chapter_narration_audio_path,
    _manifest_row_duration_sec,
    _narration_generate,
    _narration_generate_scene,
    _rough_cut,
    _rough_cut_apply_precompiled_segments,
    _rough_cut_video_segment_tuple,
    _rough_cut_visual_segments_with_chapter_cards,
    _scene_precompile,
    _slots_total_duration,
    _subtitles_generate,
    _timeline_clip_crossfade_sec,
)


_ACTIVE_TEXT_PROVIDER_ALLOWED = frozenset(
    ("", "openai", "default", "auto", "openrouter", "xai", "grok", "gemini", "google", "lm_studio")
)
_TEXT_USES_OPENAI_SDK = frozenset(("", "openai", "default", "auto", "lm_studio"))


def _active_text_llm_configured(settings: Any) -> bool:
    """True when the configured active text provider has API credentials."""
    p = str(getattr(settings, "active_text_provider", "openai")).strip().lower()
    if p in ("", "default", "auto"):
        p = "openai"
    if p == "google":
        p = "gemini"
    if p == "openai":
        return openai_compatible_configured(settings)
    if p == "lm_studio":
        return openai_compatible_configured(settings)
    if p == "openrouter":
        return bool(getattr(settings, "openrouter_api_key", None))
    if p in ("xai", "grok"):
        return bool(getattr(settings, "xai_api_key", None) or getattr(settings, "grok_api_key", None))
    if p == "gemini":
        return bool(getattr(settings, "gemini_api_key", None))
    return openai_compatible_configured(settings)


def _require_active_text_llm(settings: Any, *, for_what: str) -> None:
    """Raise if Phase 2 text LLM (outline/scripts/research enrich) cannot run."""
    if _active_text_llm_configured(settings):
        return
    raise ValueError(
        f"Active text provider is not fully configured for {for_what}. "
        "Set API keys and (for OpenAI-compatible / LM Studio) base URL under workspace Settings → Providers, then retry."
    )


def _flush_llm_usage(
    db,
    tenant_id: str,
    project_id: uuid.UUID | None,
    scene_id: uuid.UUID | None,
    asset_id: uuid.UUID | None,
    sink: list[dict[str, Any]] | None,
) -> None:
    if not sink:
        return
    persist_llm_usage_entries(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        scene_id=scene_id,
        asset_id=asset_id,
        entries=list(sink),
    )
    sink.clear()


def _scene_vo_tail_padding_sec(settings: Any) -> float:
    """Hold after spoken VO (export slots, planned_duration); from Settings / app_settings."""
    return scene_vo_tail_padding_sec_from_settings(settings)


def _scene_clip_duration_sec(settings: Any) -> float:
    """Workspace-default video clip length in seconds (fallback when
    ``scene.planned_duration_sec`` is unset). Honors the full ``3..30``
    range now allowed by ``Settings.scene_clip_duration_sec`` since the
    fixed ``{5, 10}`` validator was lifted.
    """
    try:
        v = float(getattr(settings, "scene_clip_duration_sec", 10) or 10)
    except (TypeError, ValueError):
        v = 10.0
    return max(1.0, min(v, 60.0))


def _notify_phase(
    db: Any,
    settings: Any,
    run: AgentRun | None,
    step: str,
    **extra: Any,
) -> None:
    """Best-effort Telegram notification for ONE pipeline-phase boundary.

    Called once per ``_append_event(run, "<step>", "succeeded", ...)`` after
    its companion ``db.commit()``. Never raises — Telegram outages must not
    fail the worker. Reads ``settings.telegram_notify_phase_completions`` to
    let workspace owners silence per-phase messages while keeping the
    terminal-run notification.
    """
    if run is None:
        return
    try:
        from director_api.services.telegram_notify import telegram_notify_phase_complete

        project = db.get(Project, run.project_id)
        title = (project.title if project else "Project").strip() or "Project"
        telegram_notify_phase_complete(
            settings,
            project_title=title,
            agent_run_id=str(run.id),
            step=step,
            **extra,
        )
    except Exception as exc:  # noqa: BLE001 — notify must never fail the worker
        log.warning(
            "telegram_notify_phase_complete_failed",
            step=step,
            run_id=str(run.id),
            error=str(exc)[:500],
        )


def _next_timeline_sequence_for_scene(db, scene_id: uuid.UUID) -> int:
    from director_api.tasks.worker_helpers import next_timeline_sequence_for_scene

    return next_timeline_sequence_for_scene(db, scene_id)


# Prompt/media helper implementations were extracted into dedicated modules.


# Re-export prompt runtime helpers from the extracted module.
from director_api.tasks.media_normalize_helpers import (  # noqa: E402
    _image_bytes_magic_ok,
    _normalize_image_bytes_to_dims,
    _normalize_video_bytes_to_dims,
    _package_negative_prompt,
    _project_export_dimensions,
)
from director_api.tasks.prompt_runtime_helpers import (  # noqa: E402
    StillMotion,
    _local_ffmpeg_motion_from_video_prompt,
    _manifest_requires_still_motion_encode,
    _merge_framing_safety_negative,
    _prompt_declares_no_humans,
    _prompt_leading_shot_tag,
    _resolve_phase3_video_text_prompt,
    _resolve_still_motion,
    _scene_era_anchor,
    _scene_still_prompt_for_comfy,
    _scene_video_prompt_for_provider,
    _scene_text_for_character_match,
    _should_append_framing_safety_positive,
)


_EXPORT_PROC_LOCK = threading.Lock()
_EXPORT_PROCS_BY_AGENT_RUN: dict[str, list[subprocess.Popen]] = {}


class _AgentExportFfmpegRegistry:
    """Register FFmpeg child processes so the export heartbeat can SIGTERM them when the run is reaped."""

    __slots__ = ("_aid",)

    def __init__(self, agent_run_id: uuid.UUID) -> None:
        self._aid = agent_run_id

    def attach(self, proc: subprocess.Popen) -> None:
        key = str(self._aid)
        with _EXPORT_PROC_LOCK:
            _EXPORT_PROCS_BY_AGENT_RUN.setdefault(key, []).append(proc)

    def detach(self, proc: subprocess.Popen) -> None:
        key = str(self._aid)
        with _EXPORT_PROC_LOCK:
            lst = _EXPORT_PROCS_BY_AGENT_RUN.get(key)
            if not lst:
                return
            try:
                lst.remove(proc)
            except ValueError:
                pass
            if not lst:
                _EXPORT_PROCS_BY_AGENT_RUN.pop(key, None)


def _terminate_export_processes_for_agent_run(agent_run_uuid: uuid.UUID) -> None:
    """Kill tracked FFmpeg children (stale reaper / cancel)."""
    key = str(agent_run_uuid)
    with _EXPORT_PROC_LOCK:
        procs = list(_EXPORT_PROCS_BY_AGENT_RUN.pop(key, ()))
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            log.warning("export_proc_terminate_failed", agent_run_id=key)
    time.sleep(0.6)
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            log.warning("export_proc_kill_failed", agent_run_id=key)


@contextmanager
def _agent_run_export_heartbeat(agent_run_uuid: uuid.UUID | None):
    """Bump ``AgentRun.updated_at`` periodically while FFmpeg export runs (stale reaper uses this heartbeat)."""
    if agent_run_uuid is None:
        yield
        return
    stop = threading.Event()

    try:
        with SessionLocal() as hb_db:
            r0 = hb_db.get(AgentRun, agent_run_uuid)
            if r0 and r0.status == "running":
                r0.updated_at = datetime.now(timezone.utc)
                hb_db.commit()
    except Exception:
        log.warning("agent_run_export_heartbeat_prime_failed", agent_run_id=str(agent_run_uuid))

    def _loop() -> None:
        while not stop.wait(45.0):
            try:
                with SessionLocal() as hb_db:
                    r = hb_db.get(AgentRun, agent_run_uuid)
                    if not r:
                        _terminate_export_processes_for_agent_run(agent_run_uuid)
                        continue
                    if r.status in ("failed", "cancelled"):
                        _terminate_export_processes_for_agent_run(agent_run_uuid)
                        continue
                    if r.status == "running":
                        r.updated_at = datetime.now(timezone.utc)
                        hb_db.commit()
            except Exception:
                log.warning("agent_run_export_heartbeat_failed", agent_run_id=str(agent_run_uuid))

    th = threading.Thread(target=_loop, name="agent-export-heartbeat", daemon=True)
    th.start()
    try:
        yield
    finally:
        stop.set()


def _latest_dossier(db, project_id: uuid.UUID) -> ResearchDossier | None:
    return db.scalars(
        select(ResearchDossier)
        .where(ResearchDossier.project_id == project_id)
        .order_by(ResearchDossier.version.desc())
        .limit(1)
    ).first()


def _ensure_director_pack(db, project: Project, settings: Any) -> None:
    if project.director_output_json is not None:
        validate_director_pack(project.director_output_json)
        if project.workflow_phase == "draft":
            project.workflow_phase = "director_ready"
        return
    pack = phase2_svc.build_director_pack_from_project(project)
    llm_u: list[dict[str, Any]] = []
    if openai_compatible_configured(settings):
        pack = phase2_llm.enrich_director_pack(
            pack,
            project.title,
            project.topic,
            settings,
            usage_sink=llm_u,
            frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
        )
    _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
    validate_director_pack(pack)
    project.director_output_json = pack
    project.workflow_phase = "director_ready"


def _strict_research_gate(
    db, project: Project, dossier: ResearchDossier, *, unattended: bool = False
) -> None:
    min_n = max(1, int(project.research_min_sources or 3))
    n_sources = db.scalar(
        select(func.count()).select_from(ResearchSource).where(ResearchSource.dossier_id == dossier.id)
    )
    n = int(n_sources or 0)
    body = dossier.body_json or {}
    if unattended:
        if n < min_n:
            log.warning(
                "research_gate_relaxed_unattended",
                project_id=str(project.id),
                required=min_n,
                actual=n,
                reason="source_count",
            )
        if body.get("sources_min_met") is not True:
            log.warning(
                "research_gate_relaxed_unattended",
                project_id=str(project.id),
                reason="sources_min_met_flag",
                sources_min_met=body.get("sources_min_met"),
            )
        return
    if n < min_n:
        raise AgentRunBlocked(
            "RESEARCH_MIN_SOURCES",
            f"Need at least {min_n} sources; got {n}",
            {"required": min_n, "actual": n},
        )
    if body.get("sources_min_met") is not True:
        raise AgentRunBlocked(
            "RESEARCH_MIN_SOURCES_NOT_MET",
            "Dossier sources_min_met is not true",
            {"sources_min_met": body.get("sources_min_met")},
        )


@celery_app.task(name="director.run_phase2_job", soft_time_limit=600, time_limit=720)
def run_phase2_job(job_id: str) -> None:
    from director_api.tasks.worker_runtime import _run_phase2_job_impl

    _run_phase2_job_impl(job_id)


@celery_app.task(
    bind=True,
    name="director.run_phase3_job",
    soft_time_limit=_CELERY_PHASE3_SOFT_SEC,
    time_limit=_CELERY_PHASE3_HARD_SEC,
)
def run_phase3_job(self, job_id: str) -> None:
    from director_api.tasks.worker_runtime import _run_phase3_job_impl

    _run_phase3_job_impl(self, job_id)






@celery_app.task(bind=True, name="director.run_phase4_job", soft_time_limit=600, time_limit=720)
def run_phase4_job(self, job_id: str) -> None:
    from director_api.tasks.worker_runtime import _run_phase4_job_impl

    _run_phase4_job_impl(self, job_id)


@celery_app.task(
    bind=True,
    name="director.run_phase5_job",
    soft_time_limit=_CELERY_PHASE5_SOFT_SEC,
    time_limit=_CELERY_PHASE5_HARD_SEC,
)
def run_phase5_job(self, job_id: str) -> None:
    from director_api.tasks.worker_runtime import _run_phase5_job_impl

    _run_phase5_job_impl(self, job_id)


# reap_stale_jobs has been extracted to tasks/maintenance_tasks.py
# It is imported and registered from celery_app.py — do not re-declare here.
