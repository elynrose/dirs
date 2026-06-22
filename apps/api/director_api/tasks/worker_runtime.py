"""Worker runtime — DB, providers, and phase job bodies for Celery workers.

Split plan
----------
This file is intentionally being broken into per-phase modules.  The target
layout (in progress — move one section at a time to avoid import breakage):

  tasks/maintenance_tasks.py  — reap_stale_jobs  ✅ DONE
  tasks/phase2_tasks.py       — run_phase2_job (impl: _run_phase2_job_impl)  ✅
  tasks/phase3_tasks.py       — run_phase3_job (impl: _run_phase3_job_impl)  ✅
  tasks/phase3_impl.py        — phase3 media helpers  ✅
  tasks/phase4_tasks.py       — run_phase4_job (impl: _run_phase4_job_impl)  ✅
  tasks/phase4_impl.py        — phase4 critic / revision helpers  ✅
  tasks/phase5_tasks.py       — run_phase5_job (impl: _run_phase5_job_impl)  ✅
  tasks/phase5_impl.py        — phase5 auto-heal before export  ✅
  tasks/agent_tasks.py        — run_agent_run (Celery)  ✅
  tasks/agent_impl.py         — _run_agent_run_impl + full-video tail  ✅
  tasks/smoke_tasks.py        — run_adapter_smoke_task

Section boundaries in this file are marked with  # === SECTION: <name> ===
to guide future extraction.  Do NOT add new top-level logic here — put it in
the appropriate target module instead.
"""

from __future__ import annotations

import copy
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.orm.attributes import flag_modified

from director_api.agents import phase2_llm, phase4_llm
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
    ResearchClaim,
    ResearchDossier,
    ResearchSource,
    Scene,
    TimelineVersion,
    UsageRecord,
)
from director_api.db.session import SessionLocal
from director_api.logging_config import configure_logging, get_logger
from director_api.providers import run_adapter_smoke
from director_api.services import phase2 as phase2_svc
from director_api.services.usage_accounting import persist_llm_usage_entries
from director_api.services import phase3 as phase3_svc
from director_api.services import critic_policy as critic_policy_svc
from director_api.services import phase4 as phase4_svc
from director_api.services.phase5_readiness import (
    Phase5GateError,
    compute_phase5_readiness,
    raise_phase5_gate,
    get_timeline_asset_for_project,
)
from director_api.services.scene_timeline_duration import (
    get_export_narration_budget_sec_for_scene,
    scene_vo_tail_padding_sec_from_settings,
)
from director_api.services.timeline_manifest_prefetch import manifest_prefetch_asset_hierarchy
from director_api.style_presets import effective_narration_style
from director_api.services.project_frame import coerce_frame_aspect_ratio, frame_pixel_size
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.services.job_worker_gate import acquire_job_for_work
from director_api.services.webhook_delivery import notify_job_terminal
from director_api.services.research_service import sanitize_jsonb_text
from director_api.storage.filesystem import FilesystemStorage
from director_api.tasks.celery_app import celery_app
from director_api.validation.character_schema import validate_character_bible_batch
from director_api.validation.phase2_schemas import (
    validate_chapter_outline_batch,
    validate_chapter_scripts_batch,
    validate_director_pack,
    validate_research_dossier_body,
)
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
from ffmpeg_pipelines.slideshow import compile_image_slideshow

from director_api.tasks.agent_exceptions import AgentRunBlocked, AgentRunPausedYield, AgentRunStopRequested


# On Windows ``--pool=solo``, Celery's *hard* time limit can terminate the whole worker, not just the task.
# Must stay above ``Settings.ffmpeg_timeout_sec`` (single subprocess) and typical chained FFmpeg wall time.
_CELERY_PHASE3_SOFT_SEC = 7200
_CELERY_PHASE3_HARD_SEC = 8100
_CELERY_PHASE5_SOFT_SEC = 7200
_CELERY_PHASE5_HARD_SEC = 9000


from director_api.tasks.worker_helpers import (
    _worker_runtime_for_agent_run,
    _worker_runtime_for_job,
)


configure_logging()
log = get_logger(__name__)

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
    """Runtime clip length (5 or 10 s) for scene video generation and still→video; must match Settings.scene_clip_duration_sec."""
    try:
        v = int(getattr(settings, "scene_clip_duration_sec", 10) or 10)
    except (TypeError, ValueError):
        v = 10
    return 5.0 if v == 5 else 10.0


def _next_timeline_sequence_for_scene(db, scene_id: uuid.UUID) -> int:
    from director_api.tasks.worker_helpers import next_timeline_sequence_for_scene

    return next_timeline_sequence_for_scene(db, scene_id)































# Deferred: phase impl modules may import this file indirectly — avoid import cycles.
from director_api.tasks.phase3_impl import (  # noqa: E402
    _phase3_image_generate,
    _phase3_scene_extend,
    _phase3_scenes_generate,
    _phase3_video_generate,
)
from director_api.tasks.agent_run_control import (
    agent_run_checkpoint as _agent_run_checkpoint,
    append_event as _append_event,
    payload_agent_run_uuid as _payload_agent_run_uuid,
    pipeline_control_dict as _pipeline_control_dict,
)


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


def _phase2_research_core(
    db, project: Project, settings: Any, *, agent_run_id: uuid.UUID | None = None
) -> None:
    text_provider = str(getattr(settings, "active_text_provider", "openai")).strip().lower()
    if text_provider not in _ACTIVE_TEXT_PROVIDER_ALLOWED:
        raise ValueError(
            "active_text_provider must be one of: openai, lm_studio, openrouter, xai/grok, gemini"
        )
    if not project.director_output_json:
        raise ValueError("director_output_json required before research")

    def _ar_stop() -> None:
        if agent_run_id is not None and _agent_run_checkpoint(db, agent_run_id) == "stop":
            raise AgentRunStopRequested()

    _ar_stop()

    max_v = db.scalar(
        select(func.max(ResearchDossier.version)).where(ResearchDossier.project_id == project.id)
    )
    next_v = (max_v or 0) + 1
    dossier_id = uuid.uuid4()
    min_n = max(1, int(project.research_min_sources or 3))
    body, sources, claims = phase2_svc.build_research_package(
        settings=settings,
        project=project,
        dossier_id=dossier_id,
        min_sources=min_n,
    )
    _ar_stop()
    preview: list[dict] = []
    for row in sources:
        ef = row.get("extracted_facts_json") or {}
        snippet = ef.get("snippet") if isinstance(ef, dict) else None
        preview.append(
            {
                "title": str(row.get("title") or "")[:500],
                "url": str(row.get("url_or_reference") or "")[:2048],
                "snippet": str(snippet or "")[:2000],
            }
        )
    if _active_text_llm_configured(settings):
        _ar_stop()
        llm_u: list[dict[str, Any]] = []
        body = phase2_llm.enrich_research_dossier_body(
            body, topic=project.topic, sources_preview=preview, settings=settings, usage_sink=llm_u
        )
        _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
        _ar_stop()
    validate_research_dossier_body(body)

    dossier = ResearchDossier(
        id=dossier_id,
        project_id=project.id,
        version=next_v,
        status="pending_review",
        body_json=body,
    )
    db.add(dossier)
    for row in sources:
        db.add(
            ResearchSource(
                id=row["id"],
                project_id=row["project_id"],
                dossier_id=row["dossier_id"],
                url_or_reference=row["url_or_reference"],
                title=row["title"],
                source_type=row["source_type"],
                credibility_score=row["credibility_score"],
                extracted_facts_json=row["extracted_facts_json"],
                notes=row["notes"],
                disputed=row["disputed"],
            )
        )
    for row in claims:
        db.add(
            ResearchClaim(
                id=row["id"],
                project_id=row["project_id"],
                dossier_id=row["dossier_id"],
                claim_text=row["claim_text"],
                confidence=row["confidence"],
                disputed=row["disputed"],
                adequately_sourced=row["adequately_sourced"],
                source_refs_json=row["source_refs_json"],
            )
        )
    project.workflow_phase = "research_ready"
    db.flush()


def _phase2_outline_core(
    db,
    project: Project,
    settings: Any,
    *,
    confirm_erase_assets: bool = False,
) -> None:
    from director_api.services.erase_consent import assert_outline_erase_consent

    assert_outline_erase_consent(project, consent=confirm_erase_assets)
    text_provider = str(getattr(settings, "active_text_provider", "openai")).strip().lower()
    if text_provider not in _ACTIVE_TEXT_PROVIDER_ALLOWED:
        raise ValueError(
            "active_text_provider must be one of: openai, lm_studio, openrouter, xai/grok, gemini"
        )
    if not project.director_output_json:
        raise ValueError("project or director pack missing")
    director = phase2_svc.normalized_director_pack(project)
    specs: list[dict] | None = None
    if _active_text_llm_configured(settings):
        dossier = _latest_dossier(db, project.id)
        dossier_body = (dossier.body_json if dossier else {}) or {}
        llm_u: list[dict[str, Any]] = []
        raw = phase2_llm.generate_outline_batch(
            director=director,
            dossier=dossier_body,
            target_runtime_minutes=project.target_runtime_minutes,
            settings=settings,
            usage_sink=llm_u,
        )
        _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
        if raw:
            try:
                validate_chapter_outline_batch(raw)
                chapters = sorted(raw["chapters"], key=lambda x: int(x["order_index"]))
                specs = [
                    {
                        "order_index": int(c["order_index"]),
                        "title": sanitize_jsonb_text(str(c["title"]), 500),
                        "summary": sanitize_jsonb_text(str(c["summary"]), 8000),
                        "target_duration_sec": int(c["target_duration_sec"]),
                    }
                    for c in chapters
                ]
            except Exception:
                log.warning("phase2_outline_validation_failed_falling_back", exc_info=True)
                specs = None
    if not specs:
        specs = phase2_svc.chapter_outline_from_director(director, project)
    for ch in list(project.chapters):
        db.delete(ch)
    db.flush()
    for spec in specs:
        db.add(
            Chapter(
                id=uuid.uuid4(),
                project_id=project.id,
                order_index=spec["order_index"],
                title=spec["title"],
                summary=spec["summary"],
                target_duration_sec=spec["target_duration_sec"],
                status="draft",
            )
        )
    project.workflow_phase = "outline_ready"
    db.flush()


def _phase2_chapters_core(
    db, project: Project, settings: Any, *, preserve_substantive_scripts: bool = False
) -> None:
    text_provider = str(getattr(settings, "active_text_provider", "openai")).strip().lower()
    if text_provider not in _ACTIVE_TEXT_PROVIDER_ALLOWED:
        raise ValueError(
            "active_text_provider must be one of: openai, lm_studio, openrouter, xai/grok, gemini"
        )
    chapters = (
        db.scalars(
            select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)
        ).all()
    )
    if not chapters:
        raise ValueError("no chapters — run outline first")
    dossier = _latest_dossier(db, project.id)
    dossier_body = (dossier.body_json if dossier else {}) or {}
    director = phase2_svc.normalized_director_pack(project)
    claims = (
        db.scalars(select(ResearchClaim).where(ResearchClaim.dossier_id == dossier.id)).all()
        if dossier
        else []
    )
    allowed = [c.claim_text for c in claims if c.adequately_sourced and not c.disputed]
    disputed = [c.claim_text for c in claims if c.disputed]
    try:
        tsp = int(getattr(settings, "scene_plan_target_scenes_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        tsp = 0
    tsp = max(0, min(48, tsp))
    ch_meta = []
    for ch in chapters:
        tsec = ch.target_duration_sec or 120
        tw = phase2_svc.target_narration_word_count(tsec)
        row: dict[str, Any] = {
            "order_index": ch.order_index,
            "title": ch.title,
            "summary": (ch.summary or "")[:8000],
            "target_duration_sec": tsec,
            "target_words_approx": tw,
            "min_words": max(80, int(tw * 0.78)),
        }
        if tsp > 0:
            row["target_scene_count"] = tsp
        ch_meta.append(row)
    _require_active_text_llm(settings, for_what="chapter script generation")

    SUBSTANTIVE_SCRIPT_MIN_CHARS = 200

    def _chapter_still_needs_script(ch: Chapter) -> bool:
        if preserve_substantive_scripts and len((ch.script_text or "").strip()) >= SUBSTANTIVE_SCRIPT_MIN_CHARS:
            return False
        oid = ch.order_index
        t = (by_idx.get(oid) or "").strip()
        return (oid not in by_idx) or (not t)

    def _absorb_scripts_batch(raw_batch: dict[str, Any] | None) -> None:
        if not raw_batch or raw_batch.get("schema_id") != "chapter-scripts-batch/v1":
            return
        if isinstance(raw_batch.get("scripts"), list):
            for s in raw_batch["scripts"]:
                if isinstance(s, dict) and s.get("transition_to_next") is None:
                    s.pop("transition_to_next", None)
        try:
            validate_chapter_scripts_batch(raw_batch)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"CHAPTER_SCRIPTS_INVALID: batch did not validate: {e}") from e
        for s in raw_batch["scripts"]:
            oid = int(s["order_index"])
            txt = sanitize_jsonb_text(str(s.get("script_text") or ""), 120_000).strip()
            if not txt:
                continue
            if tsp > 0:
                got = phase2_svc.script_scene_beat_paragraph_count(txt)
                if got != tsp:
                    raise ValueError(
                        f"CHAPTER_SCRIPT_SCENE_BEATS: chapter order_index={oid} must have exactly {tsp} "
                        f"blank-line-separated paragraphs (one beat per scene); got {got}. "
                        "Retry chapter generation or set target scenes to 0 in settings."
                    )
            by_idx[oid] = txt

    by_idx: dict[int, str] = {}
    for attempt in range(2):
        llm_u = []
        raw = phase2_llm.generate_scripts_batch(
            director=director,
            dossier=dossier_body,
            chapters=ch_meta,
            allowed_claims=allowed,
            disputed_claims=disputed,
            settings=settings,
            narration_style=effective_narration_style(
                project.narration_style, settings, db=db, tenant_id=project.tenant_id
            ),
            tone=project.tone,
            audience=project.audience,
            target_scenes_per_chapter=tsp,
            usage_sink=llm_u,
        )
        _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
        try:
            _absorb_scripts_batch(raw)
        except ValueError as ve:
            if attempt == 0 and "CHAPTER_SCRIPTS_INVALID" in str(ve):
                log.warning("phase2_scripts_batch_invalid_retrying", error=str(ve)[:400])
                continue
            raise
        missing = [ch.order_index for ch in chapters if _chapter_still_needs_script(ch)]
        if not missing:
            break
        if attempt == 0:
            log.warning(
                "phase2_scripts_batch_partial_retrying",
                missing_order_indices=missing,
                project_id=str(project.id),
            )
            continue
        break

    for ch in chapters:
        if not _chapter_still_needs_script(ch):
            continue
        notes = (
            "The multi-chapter JSON batch omitted or left this chapter blank. "
            "Write the complete voice-over narration for this chapter only, using the chapter title and summary, "
            "the dossier summary, director brief, allowed_claims, and disputed_claims. "
            "Meet at least min_words for this chapter's target duration.\n\n"
            f"Chapter summary:\n{(ch.summary or '')[:8000]}"
        )
        llm_fb: list[dict[str, Any]] = []
        prior = (by_idx.get(ch.order_index) or ch.script_text or "")[:120_000]
        single = phase2_llm.regenerate_chapter_script_llm(
            director=director,
            dossier_summary=dossier_body.get("summary"),
            chapter_title=ch.title,
            order_index=ch.order_index,
            current_script=prior,
            enhancement_notes=notes,
            target_duration_sec=ch.target_duration_sec or 120,
            allowed_claims=allowed,
            disputed_claims=disputed,
            settings=settings,
            narration_style=effective_narration_style(
                project.narration_style, settings, db=db, tenant_id=project.tenant_id
            ),
            tone=project.tone,
            audience=project.audience,
            target_scenes_per_chapter=tsp,
            usage_sink=llm_fb,
        )
        _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_fb)
        if single and single.strip():
            txt = sanitize_jsonb_text(single, 120_000).strip()
            if tsp > 0:
                got = phase2_svc.script_scene_beat_paragraph_count(txt)
                if got != tsp:
                    raise ValueError(
                        f"CHAPTER_SCRIPT_SCENE_BEATS: fallback chapter order_index={ch.order_index} must have "
                        f"exactly {tsp} blank-line-separated paragraphs; got {got}. "
                        "Retry or set target scenes to 0 in settings."
                    )
            by_idx[ch.order_index] = txt

    for ch in chapters:
        if not _chapter_still_needs_script(ch):
            continue
        tsec = ch.target_duration_sec or 120
        tw = phase2_svc.target_narration_word_count(tsec)
        min_w = max(80, int(tw * 0.78))
        emerg = phase2_svc.deterministic_chapter_script_emergency(
            chapter_title=ch.title,
            chapter_summary=ch.summary,
            project_topic=project.topic,
            min_words=min_w,
            target_scenes_per_chapter=tsp,
        )
        if emerg and emerg.strip():
            log.warning(
                "phase2_chapter_script_emergency_fallback",
                project_id=str(project.id),
                order_index=ch.order_index,
                target_scenes_per_chapter=tsp,
            )
            by_idx[ch.order_index] = emerg

    for ch in chapters:
        if preserve_substantive_scripts and len((ch.script_text or "").strip()) >= SUBSTANTIVE_SCRIPT_MIN_CHARS:
            continue
        if ch.order_index not in by_idx or not (by_idx[ch.order_index] or "").strip():
            raise ValueError(
                f"CHAPTER_SCRIPT_EMPTY: no script_text for chapter order_index={ch.order_index} "
                "(batch + per-chapter fallback failed). Retry chapters or check model output."
            )
        ch.script_text = by_idx[ch.order_index]
    project.workflow_phase = "chapters_ready"
    db.flush()


def _phase2_chapter_script_regenerate_core(
    db, project: Project, ch: Chapter, settings: Any, enhancement_notes: str
) -> None:
    notes = (enhancement_notes or "").strip()
    if len(notes) < 8:
        raise ValueError("enhancement_notes too short (min 8 characters)")
    text_provider = str(getattr(settings, "active_text_provider", "openai")).strip().lower()
    if text_provider not in _ACTIVE_TEXT_PROVIDER_ALLOWED:
        raise ValueError(
            "active_text_provider must be one of: openai, lm_studio, openrouter, xai/grok, gemini"
        )
    if not project.director_output_json:
        raise ValueError("director pack missing — start the project first")
    dossier = _latest_dossier(db, project.id)
    dossier_body = (dossier.body_json if dossier else {}) or {}
    director = phase2_svc.normalized_director_pack(project)
    claims = (
        db.scalars(select(ResearchClaim).where(ResearchClaim.dossier_id == dossier.id)).all()
        if dossier
        else []
    )
    allowed = [c.claim_text for c in claims if c.adequately_sourced and not c.disputed]
    disputed = [c.claim_text for c in claims if c.disputed]
    try:
        tsp = int(getattr(settings, "scene_plan_target_scenes_per_chapter", 0) or 0)
    except (TypeError, ValueError):
        tsp = 0
    tsp = max(0, min(48, tsp))
    tsec = ch.target_duration_sec or 120
    _require_active_text_llm(settings, for_what="chapter script regeneration")

    llm_u: list[dict[str, Any]] = []
    new_script = phase2_llm.regenerate_chapter_script_llm(
        director=director,
        dossier_summary=dossier_body.get("summary"),
        chapter_title=ch.title,
        order_index=ch.order_index,
        current_script=ch.script_text or "",
        enhancement_notes=notes,
        target_duration_sec=tsec,
        allowed_claims=allowed,
        disputed_claims=disputed,
        settings=settings,
        narration_style=effective_narration_style(
            project.narration_style, settings, db=db, tenant_id=project.tenant_id
        ),
        tone=project.tone,
        audience=project.audience,
        target_scenes_per_chapter=tsp,
        usage_sink=llm_u,
    )
    _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
    if not new_script:
        raise ValueError("LLM did not return a revised script (empty or invalid JSON)")
    txt = sanitize_jsonb_text(new_script, 120_000)
    if not txt.strip():
        raise ValueError("Revised script is empty after sanitization")
    if tsp > 0:
        got = phase2_svc.script_scene_beat_paragraph_count(txt)
        if got != tsp:
            raise ValueError(
                f"CHAPTER_SCRIPT_SCENE_BEATS: expected exactly {tsp} blank-line-separated paragraphs for this chapter; got {got}. "
                "Adjust enhancement notes or target scenes in settings and retry."
            )
    ch.script_text = txt
    db.flush()


def _characters_generate_core(db, project: Project, settings: Any) -> None:
    director = project.director_output_json if isinstance(project.director_output_json, dict) else {}
    if not director:
        raise ValueError("director pack required — start the project first")
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)).all()
    )
    story_bits: list[dict[str, Any]] = []
    for ch in chapters:
        st = (ch.script_text or "").strip()
        su = (ch.summary or "").strip()
        if st:
            story_bits.append(
                {
                    "order_index": ch.order_index,
                    "title": ch.title,
                    "script_excerpt": st[:14_000],
                }
            )
        elif su:
            story_bits.append(
                {
                    "order_index": ch.order_index,
                    "title": ch.title,
                    "chapter_summary": su[:4000],
                }
            )
    if not story_bits:
        raise ValueError("need at least one chapter with script_text or summary to infer characters")
    dossier = _latest_dossier(db, project.id)
    body = (dossier.body_json if dossier else {}) or {}
    dossier_summary = body.get("summary") if isinstance(body.get("summary"), str) else None

    llm_u: list[dict[str, Any]] = []
    raw, bible_err = phase2_llm.generate_character_bible(
        director=director,
        chapters_context=story_bits,
        project_title=project.title,
        project_topic=project.topic,
        dossier_summary=dossier_summary,
        settings=settings,
        usage_sink=llm_u,
    )
    _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
    if not raw:
        raise ValueError(
            bible_err
            or "character agent returned no usable JSON — check Settings text provider, model, and API keys"
        )
    bible = validate_character_bible_batch(raw)
    rows = list(bible.get("characters") or [])
    if not rows:
        raise ValueError("character bible was empty")
    db.execute(delete(ProjectCharacter).where(ProjectCharacter.project_id == project.id))
    db.flush()
    for i, c in enumerate(rows):
        db.add(
            ProjectCharacter(
                id=uuid.uuid4(),
                tenant_id=project.tenant_id,
                project_id=project.id,
                sort_order=int(c["sort_order"]),
                name=sanitize_jsonb_text(str(c.get("name") or "Character"), 256),
                role_in_story=sanitize_jsonb_text(str(c.get("role_in_story") or ""), 2000),
                visual_description=sanitize_jsonb_text(str(c.get("visual_description") or ""), 8000),
                time_place_scope_notes=(
                    sanitize_jsonb_text(str(c.get("time_place_scope_notes")), 2000)
                    if isinstance(c.get("time_place_scope_notes"), str) and str(c.get("time_place_scope_notes")).strip()
                    else None
                ),
            )
        )
    db.flush()


def run_adapter_smoke_impl(job_id: str) -> None:
    jid = uuid.UUID(job_id)
    settings = None
    should_notify = False
    try:
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if not job:
                log.error("job_not_found", job_id=job_id)
                return
            settings = _worker_runtime_for_job(db, job)
            storage = FilesystemStorage(settings.local_storage_root)
            if not acquire_job_for_work(db, job):
                return
            should_notify = True

            provider = (job.payload or {}).get("provider", "")
            try:
                result = run_adapter_smoke(str(provider), settings)
                key = f"jobs/{job_id}/smoke_result.json"
                url = storage.put_bytes(key, json.dumps(result, indent=2).encode("utf-8"))
                job.status = "succeeded"
                job.result = result
                job.completed_at = datetime.now(timezone.utc)
                art = GenerationArtifact(
                    id=uuid.uuid4(),
                    job_id=job.id,
                    project_id=job.project_id,
                    provider=str(provider),
                    model_name=str(result.get("model") or ""),
                    params_json={"smoke": True, "provider": provider},
                    storage_url=url,
                    generation_status="succeeded",
                )
                db.add(art)
                db.commit()
                log.info("adapter_smoke_done", job_id=job_id, provider=provider, configured=result.get("configured"))
            except Exception as e:  # noqa: BLE001
                job.status = "failed"
                job.error_message = str(e)[:8000]
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                log.exception("adapter_smoke_failed", job_id=job_id, provider=provider)
    finally:
        if should_notify and settings is not None:
            notify_job_terminal(jid, settings)


def _run_phase2_job_impl(job_id: str) -> None:
    jid = uuid.UUID(job_id)
    settings = None
    should_notify = False
    try:
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if not job:
                log.error("job_not_found", job_id=job_id)
                return
            settings = _worker_runtime_for_job(db, job)
            if not acquire_job_for_work(db, job):
                return
            should_notify = True
            try:
                payload = job.payload or {}
                pid = uuid.UUID(str(payload["project_id"]))
                project = db.get(Project, pid)
                if not project:
                    raise ValueError("project not found")
                if job.type == "research_run":
                    _phase2_research_core(db, project, settings)
                elif job.type == "script_outline":
                    _phase2_outline_core(
                        db,
                        project,
                        settings,
                        confirm_erase_assets=bool(payload.get("confirm_erase_assets")),
                    )
                elif job.type == "script_chapters":
                    _phase2_chapters_core(db, project, settings)
                elif job.type == "script_chapter_regenerate":
                    cid = uuid.UUID(str(payload["chapter_id"]))
                    ch = db.get(Chapter, cid)
                    if not ch or ch.project_id != project.id:
                        raise ValueError("chapter not found")
                    _phase2_chapter_script_regenerate_core(
                        db,
                        project,
                        ch,
                        settings,
                        str(payload.get("enhancement_notes") or ""),
                    )
                elif job.type == "characters_generate":
                    _characters_generate_core(db, project, settings)
                elif job.type == "thumbnail_generate":
                    from director_api.services.publish_pack import thumbnail_core

                    thumbnail_core(db, project, settings)
                elif job.type == "opening_hook_generate":
                    from director_api.services.publish_pack import opening_hook_core

                    opening_hook_core(db, project, settings)
                elif job.type == "hook_scene_append":
                    from director_api.services.publish_hook import append_hook_scene

                    append_hook_scene(db, project, settings)
                elif job.type == "outro_append":
                    from director_api.services.publish_outro import append_outro_scene

                    append_outro_scene(db, project, settings)
                else:
                    raise ValueError(f"unsupported phase2 job type: {job.type}")
                job.status = "succeeded"
                job.completed_at = datetime.now(timezone.utc)
                job.result = {"ok": True, "type": job.type}
                db.commit()
                log.info("phase2_job_done", job_id=job_id, job_type=job.type)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                job = db.get(Job, jid)
                if job:
                    job.status = "failed"
                    job.error_message = str(e)[:8000]
                    job.completed_at = datetime.now(timezone.utc)
                    db.commit()
                log.exception("phase2_job_failed", job_id=job_id, job_type=job.type)
    finally:
        if should_notify and settings is not None:
            notify_job_terminal(jid, settings)




def _record_usage(
    db,
    *,
    tenant_id: str,
    project_id: uuid.UUID | None,
    scene_id: uuid.UUID | None,
    asset_id: uuid.UUID | None,
    provider: str,
    service_type: str,
    meta: dict[str, Any] | None = None,
    units: float = 1.0,
    unit_type: str = "request",
    cost_estimate: float = 0.0,
) -> None:
    from director_api.services.usage_credits import compute_request_credits

    m = dict(meta or {})
    cr = compute_request_credits(
        provider=provider,
        service_type=service_type,
        unit_type=unit_type,
        units=units,
        meta=m,
    )
    ce = float(cost_estimate or 0.0)
    ut_low = str(unit_type or "").strip().lower()
    if ce <= 0.0 and cr > 0.0 and ut_low != "tokens":
        from director_api.services.usage_credits import CREDITS_PER_USD

        ce = float(cr) / CREDITS_PER_USD
    db.add(
        UsageRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            scene_id=scene_id,
            asset_id=asset_id,
            provider=provider,
            service_type=service_type,
            units=float(units),
            unit_type=unit_type,
            cost_estimate=ce,
            credits=cr,
            meta_json=m,
        )
    )




# Scene planning hits the text LLM with large JSON; local models (e.g. Qwen via LM Studio) may need >10 min/chapter.
# Celery entrypoint: director_api.tasks.phase3_tasks.run_phase3_job
def _run_phase3_job_impl(self, job_id: str) -> None:
    jid = uuid.UUID(job_id)
    jtype = ""
    settings = None
    should_notify = False
    try:
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if not job:
                log.error("job_not_found", job_id=job_id)
                return
            settings = _worker_runtime_for_job(db, job)
            jtype = job.type
            if not acquire_job_for_work(db, job):
                return
            should_notify = True
            try:
                extra: dict[str, Any] = {}
                if job.type == "scene_generate":
                    _phase3_scenes_generate(db, job)
                elif job.type == "scene_extend":
                    extra = _phase3_scene_extend(db, job)
                elif job.type == "scene_generate_image":
                    extra = _phase3_image_generate(db, job)
                    if extra.get("ok") is False:
                        job.status = "failed"
                        job.completed_at = datetime.now(timezone.utc)
                        job.result = {"ok": False, "type": job.type, **extra}
                        job.error_message = str(extra.get("error_message") or "image_generation_failed")[:8000]
                        db.commit()
                        log.info("phase3_job_done_failed", job_id=job_id, job_type=job.type)
                        return
                elif job.type == "scene_generate_video":
                    extra = _phase3_video_generate(db, job)
                    if extra.get("ok") is False:
                        job.status = "failed"
                        job.completed_at = datetime.now(timezone.utc)
                        job.result = {"ok": False, "type": job.type, **extra}
                        job.error_message = str(extra.get("error_message") or "video_generation_failed")[:8000]
                        db.commit()
                        log.info("phase3_job_done_failed", job_id=job_id, job_type=job.type)
                        return
                else:
                    raise ValueError(f"unsupported phase3 job type: {job.type}")
                job.status = "succeeded"
                job.completed_at = datetime.now(timezone.utc)
                job.result = {"ok": True, "type": job.type, **extra}
                db.commit()
                log.info("phase3_job_done", job_id=job_id, job_type=job.type)
            except SoftTimeLimitExceeded:
                db.rollback()
                raise
            except Exception as e:  # noqa: BLE001
                db.rollback()
                job = db.get(Job, jid)
                if job:
                    job.status = "failed"
                    job.error_message = str(e)[:8000]
                    job.completed_at = datetime.now(timezone.utc)
                    db.commit()
                log.exception("phase3_job_failed", job_id=job_id, job_type=jtype)
    except SoftTimeLimitExceeded:
        log.warning("phase3_soft_time_limit", job_id=job_id)
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if job and job.status == "running":
                job.status = "failed"
                job.error_message = "exceeded_soft_time_limit"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
    finally:
        if should_notify and settings is not None:
            notify_job_terminal(jid, settings)


def _agent_run_repair_failing_scenes(
    db,
    *,
    run: AgentRun,
    project: Project,
    settings: Any,
    agent_meta: dict[str, Any],
) -> None:
    """Revise narrations for scenes that failed critic, then re-critique (up to agent_run_scene_repair_max_rounds)."""
    max_sr = int(getattr(settings, "agent_run_scene_repair_max_rounds", 0) or 0)
    if max_sr <= 0 or bool(getattr(settings, "agent_run_fast", False)):
        return
    if not openai_compatible_configured(settings):
        return

    pol = critic_policy_svc.effective_policy(project, settings)
    use_parallel = (
        bool(settings.openai_agents_parallel)
        and agents_sdk_import_ok()
        and openai_compatible_configured(settings)
    )

    for srep in range(max_sr):
        chapters_list = list(
            db.scalars(
                select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)
            ).all()
        )
        failing: list[Scene] = []
        for ch in chapters_list:
            scenes = list(
                db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
            )
            for sc in scenes:
                if sc.critic_waived_at is not None:
                    continue
                if sc.critic_passed is True:
                    continue
                if int(sc.critic_revision_count or 0) >= pol.max_revision_cycles_per_scene:
                    continue
                failing.append(sc)
        if not failing:
            break

        _append_event(
            run,
            "scene_critic_repair",
            "running",
            repair_round=srep + 1,
            scenes=len(failing),
        )
        db.commit()

        for sc in failing:
            ch = db.get(Chapter, sc.chapter_id)
            if not ch:
                continue
            pj = db.get(Project, ch.project_id)
            if not pj:
                continue
            try:
                _scene_critic_revision_apply_from_latest_report(db, sc, pj, settings)
            except Exception:
                log.exception("agent_run_scene_repair_scene_failed", scene_id=str(sc.id))
        db.commit()

        retry_scenes = [
            sc
            for sc in failing
            if int(sc.critic_revision_count or 0) < pol.max_revision_cycles_per_scene
        ]
        if not retry_scenes:
            break

        if use_parallel:
            work_payloads = [phase4_svc.build_scene_critique_llm_payload(db, sc) for sc in retry_scenes]
            llm_u: list[dict[str, Any]] = []
            pr_rows = run_scene_critiques_parallel_sync(settings, work_payloads, usage_sink=llm_u)
            _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
            for sc, pr in zip(retry_scenes, pr_rows, strict=True):
                ld, lr = pr
                _phase4_scene_critique_core(
                    db,
                    scene_id=sc.id,
                    tenant_id=project.tenant_id,
                    job_id=None,
                    prior_report_id_in=None,
                    settings=settings,
                    meta_extra={**agent_meta, "repair_round": srep + 1},
                    prefetched_llm=(ld, lr),
                )
        else:
            for sc in retry_scenes:
                if int(sc.critic_revision_count or 0) >= pol.max_revision_cycles_per_scene:
                    continue
                _phase4_scene_critique_core(
                    db,
                    scene_id=sc.id,
                    tenant_id=project.tenant_id,
                    job_id=None,
                    prior_report_id_in=None,
                    settings=settings,
                    meta_extra={**agent_meta, "repair_round": srep + 1},
                )
        db.commit()


def _agent_run_repair_blocked_chapters(
    db,
    *,
    run: AgentRun,
    project: Project,
    blocked_id_strs: list[str],
    settings: Any,
    agent_meta: dict[str, Any],
) -> None:
    """LLM batch-edit scene narrations from latest chapter critic report before another critique attempt."""
    max_ch = int(getattr(settings, "agent_run_chapter_repair_max_rounds", 0) or 0)
    if max_ch <= 0 or bool(getattr(settings, "agent_run_fast", False)):
        return
    if not openai_compatible_configured(settings):
        return

    for cid_str in blocked_id_strs:
        try:
            cid = uuid.UUID(str(cid_str))
        except (ValueError, TypeError):
            continue
        ch = db.get(Chapter, cid)
        if not ch or ch.project_id != project.id:
            continue
        report = db.scalars(
            select(CriticReport)
            .where(
                CriticReport.project_id == project.id,
                CriticReport.tenant_id == project.tenant_id,
                CriticReport.target_type == "chapter",
                CriticReport.target_id == cid,
            )
            .order_by(desc(CriticReport.created_at))
            .limit(1)
        ).first()
        if not report:
            continue
        scenes = list(
            db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
        )
        if not scenes:
            continue
        payload_scenes = [
            {
                "order_index": int(s.order_index),
                "purpose": (s.purpose or "")[:400],
                "planned_duration_sec": int(s.planned_duration_sec or 0),
                "narration_text": (s.narration_text or "")[:4000],
                "critic_passed": s.critic_passed,
            }
            for s in scenes
        ]
        llm_u: list[dict[str, Any]] = []
        updates = phase4_llm.revise_chapter_scenes_batch_llm(
            chapter_title=ch.title or "",
            target_duration_sec=ch.target_duration_sec,
            issues_json=report.issues_json,
            recommendations_json=report.recommendations_json,
            scenes_payload=payload_scenes,
            settings=settings,
            narration_style=effective_narration_style(
                project.narration_style, settings, db=db, tenant_id=project.tenant_id
            ),
            usage_sink=llm_u,
        )
        _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
        if not updates:
            _append_event(run, "chapter_critic_repair", "skipped", chapter_id=str(cid), reason="llm_empty")
            continue
        touched_ids: list[uuid.UUID] = []
        for s in scenes:
            oi = int(s.order_index)
            if oi not in updates:
                continue
            s.narration_text = updates[oi]
            if s.critic_passed is False:
                s.critic_passed = None
            touched_ids.append(s.id)
        touched_set = set(touched_ids)
        _append_event(
            run,
            "chapter_critic_repair",
            "applied",
            chapter_id=str(cid),
            scenes_updated=len(touched_set),
        )
        pol = critic_policy_svc.effective_policy(project, settings)
        use_parallel = (
            bool(settings.openai_agents_parallel)
            and agents_sdk_import_ok()
            and openai_compatible_configured(settings)
        )
        retry_scenes_list = [
            s
            for s in scenes
            if s.id in touched_set and int(s.critic_revision_count or 0) < pol.max_revision_cycles_per_scene
        ]
        if retry_scenes_list:
            if use_parallel:
                work_payloads = [
                    phase4_svc.build_scene_critique_llm_payload(db, sc) for sc in retry_scenes_list
                ]
                llm_u2: list[dict[str, Any]] = []
                pr_rows = run_scene_critiques_parallel_sync(settings, work_payloads, usage_sink=llm_u2)
                _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u2)
                for sc, pr in zip(retry_scenes_list, pr_rows, strict=True):
                    ld, lr = pr
                    _phase4_scene_critique_core(
                        db,
                        scene_id=sc.id,
                        tenant_id=project.tenant_id,
                        job_id=None,
                        prior_report_id_in=None,
                        settings=settings,
                        meta_extra={**agent_meta, "after_chapter_repair": True},
                        prefetched_llm=(ld, lr),
                    )
            else:
                for sc in retry_scenes_list:
                    _phase4_scene_critique_core(
                        db,
                        scene_id=sc.id,
                        tenant_id=project.tenant_id,
                        job_id=None,
                        prior_report_id_in=None,
                        settings=settings,
                        meta_extra={**agent_meta, "after_chapter_repair": True},
                    )


def _chapter_resolve_narration_tts_body(db, ch: Chapter) -> str | None:
    scenes = list(
        db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
    )
    return phase3_svc.resolve_chapter_narration_tts_body(ch, scenes)




















# Celery entrypoint: director_api.tasks.phase4_tasks.run_phase4_job
def _run_phase4_job_impl(self, job_id: str) -> None:
    jid = uuid.UUID(job_id)
    jtype = ""
    settings = None
    should_notify = False
    try:
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if not job:
                log.error("job_not_found", job_id=job_id)
                return
            settings = _worker_runtime_for_job(db, job)
            jtype = job.type
            if not acquire_job_for_work(db, job):
                return
            should_notify = True
            try:
                extra: dict[str, Any] = {}
                if job.type == "scene_critique":
                    extra = _phase4_scene_critique(db, job, settings)
                elif job.type == "chapter_critique":
                    extra = _phase4_chapter_critique(db, job, settings)
                elif job.type == "scene_critic_revision":
                    extra = _phase4_scene_critic_revision(db, job, settings)
                else:
                    raise ValueError(f"unsupported phase4 job type: {job.type}")
                job.status = "succeeded"
                job.completed_at = datetime.now(timezone.utc)
                job.result = {"ok": True, "type": job.type, **extra}
                db.commit()
                log.info("phase4_job_done", job_id=job_id, job_type=job.type)
            except SoftTimeLimitExceeded:
                db.rollback()
                raise
            except Exception as e:  # noqa: BLE001
                db.rollback()
                job = db.get(Job, jid)
                if job:
                    job.status = "failed"
                    job.error_message = str(e)[:8000]
                    job.completed_at = datetime.now(timezone.utc)
                    db.commit()
                log.exception("phase4_job_failed", job_id=job_id, job_type=jtype)
    except SoftTimeLimitExceeded:
        log.warning("phase4_soft_time_limit", job_id=job_id)
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if job and job.status == "running":
                job.status = "failed"
                job.error_message = "exceeded_soft_time_limit"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
    finally:
        if should_notify and settings is not None:
            notify_job_terminal(jid, settings)


# Celery entrypoint: director_api.tasks.phase5_tasks.run_phase5_job
def _run_phase5_job_impl(self, job_id: str) -> None:
    jid = uuid.UUID(job_id)
    jtype = ""
    settings = None
    should_notify = False
    try:
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if not job:
                log.error("job_not_found", job_id=job_id)
                return
            settings = _worker_runtime_for_job(db, job)
            jtype = job.type
            if not acquire_job_for_work(db, job):
                return
            should_notify = True
            try:
                extra: dict[str, Any] = {}
                if job.type == "narration_generate":
                    extra = _narration_generate(db, job, settings)
                elif job.type == "narration_generate_scene":
                    extra = _narration_generate_scene(db, job, settings)
                elif job.type == "subtitles_generate":
                    extra = _subtitles_generate(db, job, settings)
                elif job.type == "rough_cut":
                    extra = _rough_cut(db, job, settings)
                elif job.type == "fine_cut":
                    extra = _fine_cut(db, job, settings)
                elif job.type == "final_cut":
                    extra = _final_cut(db, job, settings)
                elif job.type == "export":
                    extra = _export_bundle(db, job, settings)
                elif job.type == "scene_precompile":
                    from director_api.tasks.phase5_compile_impl import _scene_precompile

                    extra = _scene_precompile(db, job, settings)
                else:
                    raise ValueError(f"unsupported phase5 job type: {job.type}")
                job.status = "succeeded"
                job.completed_at = datetime.now(timezone.utc)
                job.result = {"ok": True, "type": job.type, **extra}
                db.commit()
                log.info("phase5_job_done", job_id=job_id, job_type=job.type)
            except SoftTimeLimitExceeded:
                db.rollback()
                raise
            except Phase5GateError as e:
                db.rollback()
                job = db.get(Job, jid)
                if job:
                    job.status = "failed"
                    job.error_message = str(e)[:8000]
                    job.result = {"ok": False, "type": job.type, "phase5_gate": e.payload}
                    job.completed_at = datetime.now(timezone.utc)
                    db.commit()
                log.warning(
                    "phase5_job_gate_failed",
                    job_id=job_id,
                    job_type=jtype,
                    gate_code=e.payload.get("code"),
                    issue_count=len(e.payload.get("issues") or []),
                )
            except Exception as e:  # noqa: BLE001
                db.rollback()
                job = db.get(Job, jid)
                if job:
                    job.status = "failed"
                    job.error_message = str(e)[:8000]
                    job.completed_at = datetime.now(timezone.utc)
                    db.commit()
                log.exception("phase5_job_failed", job_id=job_id, job_type=jtype)
    except SoftTimeLimitExceeded:
        log.warning("phase5_soft_time_limit", job_id=job_id)
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if job and job.status == "running":
                job.status = "failed"
                job.error_message = "exceeded_soft_time_limit"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
    finally:
        if should_notify and settings is not None:
            notify_job_terminal(jid, settings)


# reap_stale_jobs has been extracted to tasks/maintenance_tasks.py
# It is imported and registered from celery_app.py — do not re-declare here.

from director_api.tasks.phase4_impl import (  # noqa: E402
    _phase4_chapter_critique,
    _phase4_scene_critique,
    _phase4_scene_critic_revision,
    _phase4_scene_critique_core,
    _scene_critic_revision_apply_from_latest_report,
)
from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export  # noqa: E402
