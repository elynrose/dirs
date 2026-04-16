"""Celery tasks — run in worker process (imports DB + providers).

Split plan
----------
This file is intentionally being broken into per-phase modules.  The target
layout (in progress — move one section at a time to avoid import breakage):

  tasks/maintenance_tasks.py  — reap_stale_jobs  ✅ DONE
  tasks/phase2_tasks.py       — run_phase2_job + _phase2_* helpers
  tasks/phase3_tasks.py       — run_phase3_job + _phase3_* helpers
  tasks/phase4_tasks.py       — run_phase4_job + _phase4_* helpers
  tasks/phase5_tasks.py       — run_phase5_job + _phase5_* helpers
  tasks/agent_tasks.py        — run_agent_run + _run_agent_* helpers
  tasks/smoke_tasks.py        — run_adapter_smoke_task

Section boundaries in this file are marked with  # === SECTION: <name> ===
to guide future extraction.  Do NOT add new top-level logic here — put it in
the appropriate target module instead.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, desc, func, or_, select
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
    ResearchClaim,
    ResearchDossier,
    ResearchSource,
    RevisionIssue,
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
from director_api.services.character_prompt import (
    character_bible_for_llm_context,
    character_consistency_prefix,
    prompt_already_has_character_prefix,
)
from director_api.services import phase2 as phase2_svc
from director_api.services.usage_accounting import persist_llm_usage_entries
from director_api.services import phase3 as phase3_svc
from director_api.services import critic_policy as critic_policy_svc
from director_api.services import phase4 as phase4_svc
from director_api.services import agent_resume as agent_resume_svc
from director_api.services import pipeline_oversight as pipeline_oversight_svc
from director_api.services.phase5_readiness import (
    compute_phase5_readiness,
    format_phase5_readiness_failure,
    get_timeline_asset_for_project,
)
from director_api.services.scene_timeline_duration import (
    effective_scene_visual_budget_sec,
    get_scene_narration_audio_duration_sec,
    scene_vo_tail_padding_sec_from_settings,
)
from director_api.services.timeline_image_repair import list_export_ready_scene_visuals_ordered
from director_api.style_presets import effective_narration_style, effective_visual_style
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.services.job_worker_gate import acquire_job_for_work
from director_api.services.llm_prompt_runtime import llm_prompt_map_scope
from director_api.services.llm_prompt_service import build_resolved_prompt_map
from director_api.services.webhook_delivery import notify_job_terminal
from director_api.services.narration_bracket_visual import (
    base_image_prompt_from_scene_fields,
    video_text_prompt_from_scene_fields,
)
from director_api.services.prompt_enhance import refine_bracket_visual_prompt_llm
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
from director_api.validation.phase3_schemas import validate_scene_plan_batch
from director_api.validation.timeline_schema import validate_timeline_document

from director_api.services.subtitles_vtt import assemble_project_subtitle_markdown, script_to_webvtt

from ffmpeg_pipelines.audio_concat import concat_audio_files
from ffmpeg_pipelines.audio_slot import normalize_audio_to_duration
from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.probe import ffprobe_duration_seconds
from ffmpeg_pipelines.export_manifest import build_export_manifest
from ffmpeg_pipelines.mixed_timeline import compile_mixed_visual_timeline
from ffmpeg_pipelines.mux_master import mux_video_with_narration_and_music
from ffmpeg_pipelines.silence_audio import write_silence_aac
from ffmpeg_pipelines.overlay_video import burn_overlays_on_video
from ffmpeg_pipelines.paths import mkdir_parent, path_from_storage_url, path_is_readable_file, path_stat
from ffmpeg_pipelines.slideshow import compile_image_slideshow
from ffmpeg_pipelines.still_to_video import encode_image_to_mp4


class AgentRunStopRequested(Exception):
    """Raised to exit long synchronous phase work after ``_agent_run_checkpoint`` marks the run cancelled."""


class AgentRunPausedYield(BaseException):
    """Pause uses re-queue instead of ``time.sleep`` so ``--pool=solo`` workers stay available.

    Subclass of ``BaseException`` (not ``Exception``) so nested ``except Exception`` blocks
    in ``run_agent_run`` do not swallow the yield.
    """


# On Windows ``--pool=solo``, Celery's *hard* time limit can terminate the whole worker, not just the task.
# Must stay above ``Settings.ffmpeg_timeout_sec`` (single subprocess) and typical chained FFmpeg wall time.
_CELERY_PHASE3_SOFT_SEC = 7200
_CELERY_PHASE3_HARD_SEC = 8100
_CELERY_PHASE5_SOFT_SEC = 7200
_CELERY_PHASE5_HARD_SEC = 9000
_CELERY_AGENT_RUN_SOFT_SEC = 14_400
_CELERY_AGENT_RUN_HARD_SEC = 15_300


def _worker_runtime_for_job(db, job: Job) -> Settings:
    return resolve_runtime_settings(db, get_settings(), job.tenant_id, user_id=None)


def _worker_runtime_for_agent_run(db, run: AgentRun) -> Settings:
    return resolve_runtime_settings(
        db, get_settings(), run.tenant_id, user_id=run.started_by_user_id
    )


configure_logging()
log = get_logger(__name__)

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
    m = db.scalar(select(func.max(Asset.timeline_sequence)).where(Asset.scene_id == scene_id))
    if m is None:
        return 0
    return int(m) + 1


def _export_chapter_title_card_sec(settings: Any) -> float:
    """Workspace setting: black title-card duration before each chapter in rough/final export (0 = disabled)."""
    try:
        v = float(getattr(settings, "export_chapter_title_card_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(30.0, v))


def _timeline_clip_crossfade_sec(tj: dict[str, Any] | None) -> float:
    """Timeline JSON: dissolve between consecutive stills in rough-cut image batches (0–2s)."""
    if not isinstance(tj, dict):
        return 0.0
    try:
        v = float(tj.get("clip_crossfade_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, min(v, 2.0))


def _build_timeline_export_manifest(
    db: Any,
    project: Project,
    tv: TimelineVersion,
    settings: Any,
    *,
    allow_unapproved_media: bool = False,
) -> list[dict[str, Any]]:
    """Same ordered manifest as rough_cut (clips sorted by order_index)."""
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    validate_timeline_document(tj)
    clips = tj.get("clips") if isinstance(tj, dict) else None
    if not isinstance(clips, list):
        clips = []
    manifest: list[dict[str, Any]] = []
    for c in sorted(clips, key=lambda x: int(x.get("order_index", 0)) if isinstance(x, dict) else 0):
        if not isinstance(c, dict):
            continue
        src = c.get("source")
        if not isinstance(src, dict) or src.get("kind") != "asset":
            raise ValueError("each clip needs source.kind asset")
        aid = uuid.UUID(str(src["asset_id"]))
        asset = get_timeline_asset_for_project(db, aid, project.id)
        if asset is None:
            raise ValueError(f"asset not in project: {aid}")
        if not allow_unapproved_media and asset.approved_at is None:
            raise ValueError(f"asset not approved: {aid}")
        clip_dur = c.get("duration_sec")
        duration_sec: float | None
        if clip_dur is not None:
            duration_sec = float(clip_dur)
        elif asset.asset_type == "image":
            duration_sec = _scene_clip_duration_sec(settings)
        else:
            duration_sec = None
        manifest.append(
            {
                "order_index": c.get("order_index"),
                "asset_id": str(aid),
                "storage_url": asset.storage_url,
                "asset_type": asset.asset_type,
                "duration_sec": duration_sec,
                "trim_start_sec": c.get("trim_start_sec"),
                "trim_end_sec": c.get("trim_end_sec"),
            }
        )
    return manifest


def _manifest_row_duration_sec(
    m: dict[str, Any],
    *,
    storage_root: Path,
    ffprobe_bin: str,
    timeout_sec: float,
) -> float:
    lp = path_from_storage_url(m.get("storage_url"), storage_root=storage_root)
    if lp is None or not path_is_readable_file(lp):
        raise ValueError(f"missing local file for asset {m.get('asset_id')}")
    at = str(m.get("asset_type") or "").lower()
    if at == "image":
        ds = m.get("duration_sec")
        if ds is None or float(ds) <= 0:
            raise ValueError(f"invalid duration_sec for image asset {m.get('asset_id')}")
        return float(ds)
    if at == "video":
        if m.get("duration_sec") is not None:
            return float(m["duration_sec"])
        return float(
            ffprobe_duration_seconds(lp, ffprobe_bin=ffprobe_bin, timeout_sec=min(timeout_sec, 120.0))
        )
    raise ValueError(f"unsupported asset_type for audio slot: {at}")


def _manifest_prefetch_asset_hierarchy(
    db: Any,
    manifest: list[dict[str, Any]],
) -> tuple[dict[uuid.UUID, Asset], dict[uuid.UUID, Scene], dict[uuid.UUID, Chapter]]:
    """Batch-load Asset → Scene → Chapter for all manifest rows (avoids N+1 ``db.get``)."""
    aids: set[uuid.UUID] = set()
    for m in manifest:
        aid = m.get("asset_id")
        if aid is None:
            continue
        try:
            aids.add(uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            continue
    if not aids:
        return {}, {}, {}
    assets = list(db.scalars(select(Asset).where(Asset.id.in_(aids))).all())
    asset_by_id = {a.id: a for a in assets}
    scene_ids = {a.scene_id for a in assets if a.scene_id}
    if not scene_ids:
        return asset_by_id, {}, {}
    scenes = list(db.scalars(select(Scene).where(Scene.id.in_(scene_ids))).all())
    scene_by_id = {s.id: s for s in scenes}
    ch_ids = {s.chapter_id for s in scenes if s.chapter_id}
    if not ch_ids:
        return asset_by_id, scene_by_id, {}
    chapters = list(db.scalars(select(Chapter).where(Chapter.id.in_(ch_ids))).all())
    ch_by_id = {c.id: c for c in chapters}
    return asset_by_id, scene_by_id, ch_by_id


def _final_cut_audio_slots_from_manifest(
    db: Any,
    manifest: list[dict[str, Any]],
    *,
    card_sec: float,
    storage_root: Path,
    ffprobe_bin: str,
    timeout_sec: float,
) -> list[tuple[float, uuid.UUID | None]]:
    """(slot_duration, scene_id or None for chapter title card). Matches rough_cut visual order."""
    asset_by_id, scene_by_id, ch_by_id = _manifest_prefetch_asset_hierarchy(db, manifest)
    slots: list[tuple[float, uuid.UUID | None]] = []
    prev_chapter_id: uuid.UUID | None = None
    for m in manifest:
        aid = uuid.UUID(str(m["asset_id"]))
        asset = asset_by_id.get(aid)
        ch_id: uuid.UUID | None = None
        if asset and asset.scene_id:
            sc = scene_by_id.get(asset.scene_id)
            if sc:
                ch = ch_by_id.get(sc.chapter_id) if sc.chapter_id else None
                if ch:
                    ch_id = ch.id
        cs = float(card_sec)
        if cs > 0 and ch_id is not None and ch_id != prev_chapter_id:
            slots.append((cs, None))
            prev_chapter_id = ch_id
        elif ch_id is not None:
            prev_chapter_id = ch_id

        clip_dur = _manifest_row_duration_sec(
            m, storage_root=storage_root, ffprobe_bin=ffprobe_bin, timeout_sec=timeout_sec
        )
        sid = asset.scene_id if asset else None
        slots.append((clip_dur, sid))
    return slots


def _slots_total_duration(slots: list[tuple[float, uuid.UUID | None]]) -> float:
    return float(sum(max(0.0, float(t[0])) for t in slots))


def _expand_manifest_and_slots_for_full_narration(
    db: Any,
    manifest: list[dict[str, Any]],
    *,
    card_sec: float,
    project_id: uuid.UUID,
    storage_root: Path,
    ffprobe_bin: str,
    timeout_sec: float,
    tail_padding_sec: float,
) -> tuple[list[dict[str, Any]], list[tuple[float, uuid.UUID | None]]]:
    """Widen the first timeline clip per scene so visuals run at least VO length + tail padding (export)."""
    asset_by_id, scene_by_id, ch_by_id = _manifest_prefetch_asset_hierarchy(db, manifest)
    adjusted: list[dict[str, Any]] = [copy.deepcopy(m) for m in manifest]
    slots: list[tuple[float, uuid.UUID | None]] = []
    voice_used: set[uuid.UUID] = set()
    prev_chapter_id: uuid.UUID | None = None
    mi = 0
    for m in manifest:
        aid = uuid.UUID(str(m["asset_id"]))
        asset = asset_by_id.get(aid)
        ch_id: uuid.UUID | None = None
        if asset and asset.scene_id:
            sc = scene_by_id.get(asset.scene_id)
            if sc:
                ch = ch_by_id.get(sc.chapter_id) if sc.chapter_id else None
                if ch:
                    ch_id = ch.id
        cs = float(card_sec)
        if cs > 0 and ch_id is not None and ch_id != prev_chapter_id:
            slots.append((cs, None))
            prev_chapter_id = ch_id
        elif ch_id is not None:
            prev_chapter_id = ch_id

        clip_dur = _manifest_row_duration_sec(
            m, storage_root=storage_root, ffprobe_bin=ffprobe_bin, timeout_sec=timeout_sec
        )
        sid = asset.scene_id if asset else None
        voice_sid: uuid.UUID | None = sid if sid and sid not in voice_used else None
        if voice_sid is not None:
            voice_used.add(voice_sid)
        new_dur = float(clip_dur)
        if voice_sid is not None:
            narr = get_scene_narration_audio_duration_sec(
                db,
                project_id=project_id,
                scene_id=voice_sid,
                storage_root=storage_root,
                ffprobe_bin=ffprobe_bin,
                timeout_sec=timeout_sec,
            )
            if narr is not None and narr > 0:
                new_dur = max(new_dur, float(narr) + float(tail_padding_sec))

        slots.append((new_dur, sid))
        at = str(adjusted[mi].get("asset_type") or "").lower()
        if at in ("image", "video"):
            adjusted[mi]["duration_sec"] = new_dur
        mi += 1
    return adjusted, slots


def _count_scene_narration_tracks(db: Any, project_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(NarrationTrack)
            .where(
                NarrationTrack.project_id == project_id,
                NarrationTrack.scene_id.isnot(None),
                NarrationTrack.audio_url.isnot(None),
            )
        )
        or 0
    )


def _build_scene_timeline_narration_stem(
    db: Any,
    project_id: uuid.UUID,
    slots: list[tuple[float, uuid.UUID | None]],
    out_dir: Path,
    *,
    ffmpeg_bin: str,
    timeout_sec: float,
    storage_root: Path,
) -> tuple[Path | None, list[Path]]:
    """Concat silence + per-scene narration segments to one AAC track; returns (merged_path, paths_to_delete).

    Slot durations should already include **at least** spoken VO + configured tail padding for the
    first timeline clip of each scene (see ``_expand_manifest_and_slots_for_full_narration``) so
    ``normalize_audio_to_duration`` pads rather than trims narration.

    If the same ``scene_id`` appears in multiple timeline clips, narration is only placed on the
    **first** clip for that scene; later clips get silence on the VO bus (music still mixes).
    """
    parts: list[Path] = []
    cleanup: list[Path] = []
    voice_used_for_scene: set[uuid.UUID] = set()
    for slot_dur, sid in slots:
        if slot_dur <= 0:
            continue
        if sid is None:
            sp = out_dir / f"_sil_{uuid.uuid4().hex}.aac"
            write_silence_aac(
                sp,
                duration_sec=slot_dur,
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=min(timeout_sec, 600.0),
            )
            parts.append(sp)
            cleanup.append(sp)
            continue
        voice_sid: uuid.UUID | None = sid if sid not in voice_used_for_scene else None
        if voice_sid is not None:
            voice_used_for_scene.add(voice_sid)
        if voice_sid is None:
            sp = out_dir / f"_sil_{uuid.uuid4().hex}.aac"
            write_silence_aac(
                sp,
                duration_sec=slot_dur,
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=min(timeout_sec, 600.0),
            )
            parts.append(sp)
            cleanup.append(sp)
            continue
        nt = db.scalar(
            select(NarrationTrack)
            .where(
                NarrationTrack.project_id == project_id,
                NarrationTrack.scene_id == voice_sid,
                NarrationTrack.audio_url.isnot(None),
            )
            .order_by(NarrationTrack.created_at.desc())
        )
        np = path_from_storage_url((nt.audio_url or "") if nt else "", storage_root=storage_root)
        if nt and np is not None and path_is_readable_file(np):
            seg = out_dir / f"_seg_{uuid.uuid4().hex}.m4a"
            try:
                normalize_audio_to_duration(
                    np,
                    seg,
                    slot_dur,
                    ffmpeg_bin=ffmpeg_bin,
                    timeout_sec=min(timeout_sec, 600.0),
                )
            except FFmpegCompileError as _narr_enc_err:
                log.warning(
                    "scene_timeline_narration_encode_failed_substituting_silence",
                    scene_id=str(voice_sid),
                    slot_dur_sec=slot_dur,
                    error=str(_narr_enc_err)[:300],
                )
                if path_is_readable_file(seg):
                    seg.unlink(missing_ok=True)
                sp = out_dir / f"_sil_{uuid.uuid4().hex}.aac"
                write_silence_aac(
                    sp,
                    duration_sec=slot_dur,
                    ffmpeg_bin=ffmpeg_bin,
                    timeout_sec=min(timeout_sec, 600.0),
                )
                parts.append(sp)
                cleanup.append(sp)
            else:
                parts.append(seg)
                cleanup.append(seg)
        else:
            log.warning(
                "scene_timeline_narration_missing_substituting_silence",
                scene_id=str(voice_sid),
                slot_dur_sec=slot_dur,
                has_track=nt is not None,
                has_path=np is not None,
                path_readable=bool(np is not None and path_is_readable_file(np)),
            )
            sp = out_dir / f"_sil_{uuid.uuid4().hex}.aac"
            write_silence_aac(
                sp,
                duration_sec=slot_dur,
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=min(timeout_sec, 600.0),
            )
            parts.append(sp)
            cleanup.append(sp)
    if not parts:
        return None, cleanup
    merged = out_dir / f"_narr_scene_{uuid.uuid4().hex}.m4a"
    try:
        concat_audio_files(parts, merged, ffmpeg_bin=ffmpeg_bin, timeout_sec=timeout_sec)
    except Exception:
        # Per-slot files are deleted in the finally block below; propagate the concat error
        # so callers see the real failure cause rather than a "file not found" error from mux.
        raise
    finally:
        # Always delete the per-slot segment files regardless of whether concat succeeded.
        # On success they've been baked into `merged`; on failure they would otherwise leak
        # inside the exports directory (out_dir) and accumulate across retries.
        for p in parts:
            if path_is_readable_file(p) and p != merged:
                try:
                    p.unlink()
                except OSError:
                    pass
    # Only set cleanup to [merged] after a successful concat — if concat raised, merged was
    # never written and there is nothing to clean up.
    cleanup = [merged]
    return merged, cleanup


def _rough_cut_visual_segments_with_chapter_cards(
    db,
    manifest: list[dict[str, Any]],
    *,
    card_sec: float,
    storage_root: Path,
    ffprobe_bin: str = "ffprobe",
) -> list[Any]:
    """Build mixed-timeline segments: optional black title cards at chapter boundaries + clip assets."""
    asset_by_id, scene_by_id, ch_by_id = _manifest_prefetch_asset_hierarchy(db, manifest)
    segments: list[Any] = []
    prev_chapter_id: uuid.UUID | None = None
    for m in manifest:
        aid = uuid.UUID(str(m["asset_id"]))
        asset = asset_by_id.get(aid)
        ch_id: uuid.UUID | None = None
        title_txt = "Chapter"
        if asset and asset.scene_id:
            sc = scene_by_id.get(asset.scene_id)
            if sc:
                ch = ch_by_id.get(sc.chapter_id) if sc.chapter_id else None
                if ch:
                    ch_id = ch.id
                    title_txt = (ch.title or "").strip() or f"Part {int(ch.order_index) + 1}"
        if card_sec > 0 and ch_id is not None and ch_id != prev_chapter_id:
            segments.append(("chapter_title", title_txt, float(card_sec)))
            prev_chapter_id = ch_id
        elif ch_id is not None:
            prev_chapter_id = ch_id

        lp = path_from_storage_url(m.get("storage_url"), storage_root=storage_root)
        if lp is None or not path_is_readable_file(lp):
            raise ValueError(f"missing local file for asset {m.get('asset_id')}")
        at = str(m["asset_type"]).lower()
        if at == "video":
            ds = m.get("duration_sec")
            if ds is not None and float(ds) > 0:
                try:
                    native = float(
                        ffprobe_duration_seconds(
                            lp,
                            ffprobe_bin=ffprobe_bin,
                            timeout_sec=120.0,
                        )
                    )
                except (FileNotFoundError, OSError, RuntimeError, ValueError, TypeError):
                    native = 0.0
                if native > 0 and abs(float(ds) - native) <= 0.12:
                    segments.append(("video", lp, None))
                else:
                    segments.append(("video", lp, float(ds)))
            else:
                segments.append(("video", lp, None))
        elif at == "image":
            ds = m.get("duration_sec")
            if ds is None or float(ds) <= 0:
                raise ValueError(f"invalid duration_sec for image asset {m.get('asset_id')}")
            segments.append(("image", lp, float(ds)))
        else:
            raise ValueError("ROUGH_CUT_FFMPEG: unsupported asset_type for compile")
    return segments


def _rough_cut_video_segment_tuple(
    m: dict[str, Any],
    lp: Path,
    *,
    ffprobe_bin: str,
) -> tuple[str, Path, float | None]:
    at = str(m.get("asset_type") or "").lower()
    if at != "video":
        raise ValueError("expected video asset")
    ds = m.get("duration_sec")
    if ds is not None and float(ds) > 0:
        try:
            native = float(ffprobe_duration_seconds(lp, ffprobe_bin=ffprobe_bin, timeout_sec=120.0))
        except (FileNotFoundError, OSError, RuntimeError, ValueError, TypeError):
            native = 0.0
        if native > 0 and abs(float(ds) - native) <= 0.12:
            return ("video", lp, None)
        return ("video", lp, float(ds))
    return ("video", lp, None)


class AgentRunBlocked(Exception):
    """Strict gate failure — persist as agent_run.status = blocked."""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail or {}


def _bind_asset_local_file(asset: Asset, url: str, storage_key: str) -> None:
    """Set storage URLs and a stable relative key so the API can resolve files if file:// parsing drifts."""
    asset.storage_url = url
    asset.preview_url = url
    pj = dict(asset.params_json) if isinstance(asset.params_json, dict) else {}
    pj["storage_key"] = storage_key
    asset.params_json = pj


def _image_bytes_magic_ok(data: bytes) -> bool:
    """Best-effort image signature check for pass-through bytes (before/without successful ffmpeg normalize)."""
    if not data or len(data) < 4:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    # JPEG: SOI is FF D8; next byte varies (E0, E1, DB, …).
    if data[:2] == b"\xff\xd8":
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    # TIFF (common from some decoders)
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return True
    # BMP
    if data[:2] == b"BM" and len(data) >= 14:
        return True
    # JPEG 2000 (rare)
    if len(data) >= 12 and data[4:8] == b"jP  ":
        return True
    # AVIF / HEIF (ISO BMFF): ftyp not always at offset 4; scan first 512 bytes.
    window = data[: min(512, len(data))]
    if b"ftyp" in window:
        i = window.find(b"ftyp")
        if i >= 0 and i + 12 <= len(data):
            brands = data[i : i + 32]
            if b"avif" in brands or b"avis" in brands or b"mif1" in brands or b"msf1" in brands or b"heic" in brands:
                return True
    return False


def _project_export_dimensions(project: Project) -> tuple[int, int]:
    """Width × height for normalize, local still→video, and rough/final timeline compiles."""
    from director_api.services.project_frame import coerce_frame_aspect_ratio, frame_pixel_size

    return frame_pixel_size(coerce_frame_aspect_ratio(getattr(project, "frame_aspect_ratio", None)))


def _normalize_image_bytes_to_dims(
    settings: Any,
    data: bytes,
    content_type: str | None,
    target_w: int,
    target_h: int,
) -> tuple[bytes, str, bool]:
    """Crop/scale to target_w×target_h via ffmpeg. Returns (bytes, content_type, ffmpeg_output_trusted).

    If ffmpeg runs and writes a non-trivial output file, we trust it (explicit mjpeg) even if magic
    checks would fail on exotic inputs. If ffmpeg fails or writes empty output, we fall back to raw
    bytes and set trusted False.
    """
    ffmpeg_bin = (getattr(settings, "ffmpeg_bin", None) or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        return data, (content_type or "image/jpeg"), False
    in_suffix = ".jpg"
    ct = (content_type or "").lower()
    if "png" in ct:
        in_suffix = ".png"
    elif "webp" in ct:
        in_suffix = ".webp"
    elif "avif" in ct or "heif" in ct or "heic" in ct:
        in_suffix = ".avif"
    with tempfile.NamedTemporaryFile(suffix=in_suffix, delete=False) as fin:
        fin.write(data)
        in_path = Path(fin.name)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fout:
        out_path = Path(fout.name)
    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(in_path),
            "-vf",
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}",
            "-frames:v",
            "1",
            "-c:v",
            "mjpeg",
            "-q:v",
            "2",
            str(out_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=120)
        out_b = out_path.read_bytes()
        if len(out_b) >= 32:
            return out_b, "image/jpeg", True
        log.warning("ffmpeg_normalize_empty_or_tiny_output", out_len=len(out_b))
        return data, (content_type or "image/jpeg"), False
    except Exception as e:
        log.warning("ffmpeg_normalize_failed", error=str(e)[:300])
        return data, (content_type or "image/jpeg"), False
    finally:
        in_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def _normalize_video_bytes_to_dims(settings: Any, data: bytes, target_w: int, target_h: int) -> bytes:
    ffmpeg_bin = (getattr(settings, "ffmpeg_bin", None) or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        return data
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fin:
        fin.write(data)
        in_path = Path(fin.name)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fout:
        out_path = Path(fout.name)
    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(in_path),
            "-vf",
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(out_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=240)
        return out_path.read_bytes()
    except Exception:
        return data
    finally:
        in_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def _package_negative_prompt(pp: Any) -> str | None:
    if not isinstance(pp, dict):
        return None
    n = pp.get("negative_prompt")
    if not isinstance(n, str) or not n.strip():
        return None
    return sanitize_jsonb_text(n.strip(), 1200)


def _scene_still_prompt_for_comfy(db: Any, scene: Scene, project: Project, settings: Any) -> str:
    """Same prompt recipe as scene image generation (Flux / Comfy still), without job payload overrides."""
    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    prompt, _, _ = base_image_prompt_from_scene_fields(
        narration_text=scene.narration_text,
        prompt_package_json=pp,
        image_prompt_override=None,
    )
    prefix = character_consistency_prefix(db, project.id, max_chars=2000)
    if prefix and not prompt_already_has_character_prefix(prompt, prefix):
        room = max(400, 4000 - len(prefix) - 3)
        prompt = f"{prefix}\n\n{str(prompt)[:room]}"
    vis_style = effective_visual_style(project.visual_style, settings)
    if vis_style:
        vs = vis_style.strip()
        if vs:
            tail = prompt[-min(len(prompt), 800) :] if prompt else ""
            if vs[:100] not in tail:
                room_vs = max(0, 4000 - len(prompt) - 24)
                if room_vs > 80:
                    prompt = f"{prompt}\n\nVisual style: {vs[:room_vs]}"
    return str(prompt)


def _resolve_phase3_video_text_prompt(
    scene: Scene,
    pp: dict[str, Any],
    *,
    override: Any = None,
) -> str:
    """Text sent to generative video models; optional job override, else package, else ``[bracket]`` hints, else VO/purpose."""
    return video_text_prompt_from_scene_fields(
        narration_text=scene.narration_text,
        purpose=scene.purpose,
        visual_type=scene.visual_type,
        prompt_package_json=pp if isinstance(pp, dict) else {},
        video_prompt_override=override if isinstance(override, str) else None,
    )


def _local_ffmpeg_motion_from_video_prompt(prompt: str) -> tuple[bool, str, str]:
    """Coarse motion hints from natural-language ``video_prompt`` for still→MP4 / slideshow.

    Returns ``(slow_zoom, ken_burns_direction 'in'|'out', slideshow_motion 'none'|'pan'|'zoom')``.
    """
    t = (prompt or "").lower()
    has_pan = any(
        p in t
        for p in (
            "pan left",
            "pan right",
            "panning",
            "camera pans",
            "lateral move",
            "truck left",
            "truck right",
            "whip pan",
        )
    )
    zoom_out = any(p in t for p in ("zoom out", "pull out", "pull back", "dolly out", "pull-back", "widen"))
    zoom_in = any(
        p in t
        for p in (
            "zoom in",
            "push in",
            "push-in",
            "dolly in",
            "slow zoom",
            "creep in",
            "tighter",
            "closing in",
            "push closer",
        )
    )
    if has_pan and not zoom_in and not zoom_out and "zoom" not in t:
        return False, "in", "pan"
    if zoom_out:
        return True, "out", "zoom"
    if zoom_in or ("zoom" in t and not zoom_out):
        return True, "in", "zoom"
    return False, "in", "none"


def _append_event(run: AgentRun, step: str, status: str, **extra: Any) -> None:
    events = list(run.steps_json) if run.steps_json else []
    row: dict[str, Any] = {
        "step": step,
        "status": status,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    for k, v in extra.items():
        if v is not None:
            row[k] = v
    events.append(row)
    run.steps_json = events
    flag_modified(run, "steps_json")


def _pipeline_control_dict(raw: Any) -> dict[str, bool]:
    if not isinstance(raw, dict):
        return {"paused": False, "stop_requested": False}
    return {
        "paused": bool(raw.get("paused")),
        "stop_requested": bool(raw.get("stop_requested")),
    }


def _payload_agent_run_uuid(payload: dict[str, Any]) -> uuid.UUID | None:
    v = payload.get("agent_run_id")
    if v is None:
        return None
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def _merge_pipeline_control(run: AgentRun, **updates: bool) -> None:
    cur = dict(run.pipeline_control_json) if isinstance(run.pipeline_control_json, dict) else {}
    for k, v in updates.items():
        cur[k] = bool(v)
    run.pipeline_control_json = cur
    flag_modified(run, "pipeline_control_json")


def _agent_run_checkpoint(db: Any, agent_run_uuid: uuid.UUID) -> str:
    """Honor pause/stop from API.

    While paused, commits DB state and raises `AgentRunPausedYield` so the Celery task can exit
    and re-queue with a countdown (avoids blocking ``--pool=solo`` with ``time.sleep``).

    Returns 'ok' or 'stop'.
    """
    db.expire_all()
    r = db.get(AgentRun, agent_run_uuid)
    if not r:
        return "stop"
    ctrl = _pipeline_control_dict(r.pipeline_control_json)
    if ctrl["stop_requested"]:
        if r.status not in ("cancelled", "failed", "succeeded", "blocked"):
            r.status = "cancelled"
            r.error_message = "Stopped by user"
            r.completed_at = datetime.now(timezone.utc)
            r.current_step = None
            _merge_pipeline_control(r, paused=False)
            _append_event(r, "pipeline", "cancelled", reason="user_stop")
            db.commit()
        return "stop"
    if ctrl["paused"]:
        if r.status == "running":
            r.status = "paused"
            _append_event(r, "pipeline", "paused")
            db.commit()
        raise AgentRunPausedYield()
    if r.status == "paused":
        r.status = "running"
        cur = dict(r.pipeline_control_json) if isinstance(r.pipeline_control_json, dict) else {}
        cur["paused"] = False
        r.pipeline_control_json = cur
        flag_modified(r, "pipeline_control_json")
        _append_event(r, "pipeline", "resumed")
        db.commit()
    return "ok"


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


def _phase2_outline_core(db, project: Project, settings: Any) -> None:
    text_provider = str(getattr(settings, "active_text_provider", "openai")).strip().lower()
    if text_provider not in _ACTIVE_TEXT_PROVIDER_ALLOWED:
        raise ValueError(
            "active_text_provider must be one of: openai, lm_studio, openrouter, xai/grok, gemini"
        )
    if not project.director_output_json:
        raise ValueError("project or director pack missing")
    director = project.director_output_json
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
    director = project.director_output_json or {}
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
    director = project.director_output_json or {}
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


@celery_app.task(name="director.run_adapter_smoke")
def run_adapter_smoke_task(job_id: str) -> None:
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


@celery_app.task(name="director.run_phase2_job", soft_time_limit=600, time_limit=720)
def run_phase2_job(job_id: str) -> None:
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
                    _phase2_outline_core(db, project, settings)
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


def _agent_run_mark_failed(db, run: AgentRun, step: str, exc: Exception) -> None:
    run.status = "failed"
    run.current_step = None
    run.error_message = str(exc)[:8000]
    run.completed_at = datetime.now(timezone.utc)
    _append_event(run, step, "failed", error_code="EXCEPTION", message=str(exc)[:500])
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
    for ch in chapters:
        if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
            return False
        if len((ch.script_text or "").strip()) < 12:
            continue
        if not phase3_svc.chapter_eligible_for_scene_planning(ch):
            continue
        n_sc = db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch.id)) or 0
        if int(n_sc) > 0:
            continue
        try:
            _phase3_scenes_plan_for_chapter(db, ch, project, settings)
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
    if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
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

    # Character bible (LLM) — image/video prompts use consistency prefixes from ProjectCharacter rows.
    # Run when oversight allows this tail slot, or whenever we still have no ProjectCharacter rows — do not
    # require ``tail_should_run(auto_images)``: LLM oversight can suggest resuming at auto_narration while image
    # work is still pending, which previously skipped bible generation entirely.
    char_tail_ok = pipeline_oversight_svc.tail_should_run_with_force("auto_characters", tr, fs)
    need_character_gen = force_regen_characters or not _project_has_character_rows(db, pid)
    if char_tail_ok or need_character_gen:
        run = db.get(AgentRun, agent_run_uuid)
        if need_character_gen:
            if run:
                run.current_step = "auto_characters"
                _append_event(run, "auto_characters", "running")
            db.commit()
            try:
                proj_for_chars = db.get(Project, pid)
                if not proj_for_chars:
                    raise ValueError("project missing before character bible generation")
                _characters_generate_core(db, proj_for_chars, settings)
                db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                run = db.get(AgentRun, agent_run_uuid)
                if run:
                    _agent_run_mark_failed(db, run, "auto_characters", e)
                raise
            run = db.get(AgentRun, agent_run_uuid)
            if run:
                _append_event(run, "auto_characters", "succeeded")
                db.commit()
        else:
            if run:
                _append_event(run, "auto_characters", "skipped", reason="characters_already_present")
            db.commit()
    else:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            _append_event(run, "auto_characters", "skipped", reason="oversight_tail_resume")
        db.commit()

    project = db.get(Project, pid)
    if not project:
        raise ValueError("project missing after character bible step")

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
            aid_s = out.get("asset_id")
            if aid_s:
                ast = db.get(Asset, uuid.UUID(str(aid_s)))
                if ast and ast.status == "succeeded" and ast.approved_at is None:
                    ast.approved_at = datetime.now(timezone.utc)
            return "ok"

        failed_ids: list[uuid.UUID] = []
        for sc in target_scenes:
            if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
                return None
            scene_failed = False
            while _scene_succeeded_image_count(db, sc.id) < min_scene_images:
                if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
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
                if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
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

    if pipeline_oversight_svc.tail_should_run_with_force("auto_images", tr, fs) and auto_scene_images_pre:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            run.current_step = "auto_images"
            _append_event(run, "auto_images", "running", scene_total=len(all_scenes))
        db.commit()
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
                _append_event(
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
            _append_event(run, "auto_images", "succeeded")
            db.commit()
    else:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            skip_reason = (
                "auto_generate_scene_images_false"
                if not auto_scene_images_pre
                else "oversight_tail_resume"
            )
            _append_event(run, "auto_images", "skipped", reason=skip_reason)
        db.commit()

    auto_scene_videos = auto_scene_videos_pre
    run_tail_videos = pipeline_oversight_svc.tail_should_run_with_force("auto_videos", tr, fs) and (
        auto_scene_videos or force_regen_videos
    )
    if run_tail_videos:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            run.current_step = "auto_videos"
            _append_event(run, "auto_videos", "running", scene_total=len(all_scenes))
        db.commit()
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
                if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
                    return None
                scene_failed = False
                while _scene_succeeded_video_count(db, sc.id) < min_scene_videos:
                    if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
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
                    if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
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
                _append_event(
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
                _append_event(
                    run,
                    "auto_videos",
                    "partial_failed",
                    generated=video_generated,
                    skipped_existing=video_skipped,
                    failed_scene_count=len(vid_failed),
                    failed_scene_ids=[str(x) for x in vid_failed[:64]],
                    note=(
                        "Some scenes still lack enough succeeded video assets after retries; continuing to narration and timeline. "
                        "Re-generate failed clips in Studio, or set agent_run_abort_on_auto_video_failure (or pipeline_options.abort_on_auto_video_failure) to stop the run on this condition."
                    ),
                )
            else:
                _append_event(
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
            _append_event(run, "auto_videos", "skipped", reason=skip_reason)
        db.commit()

    if pipeline_oversight_svc.tail_should_run_with_force("auto_narration", tr, fs):
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            run.current_step = "auto_narration"
            _append_event(run, "auto_narration", "running")
        db.commit()
        all_scenes_narr = _ordered_scenes_for_project(db, pid)
        narr_scene_targets: list[Scene] = []
        for sc in all_scenes_narr:
            if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
                return False
            if len((sc.narration_text or "").strip()) < 2:
                continue
            if _scene_has_scene_narration_audio(db, sc.id) and not force_regen_narration:
                continue
            narr_scene_targets.append(sc)

        def _auto_scene_narration_pass(target_scenes: list[Scene]) -> list[uuid.UUID] | None:
            failed_s: list[uuid.UUID] = []
            for sc in target_scenes:
                if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
                    return None
                if _scene_has_scene_narration_audio(db, sc.id) and not force_regen_narration:
                    continue
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
                    ns_out = _narration_generate_scene(db, js, settings)
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
                _append_event(
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
            _append_event(run, "auto_narration", "succeeded", narration_granularity="scene")
        db.commit()
    else:
        run = db.get(AgentRun, agent_run_uuid)
        if run:
            _append_event(run, "auto_narration", "skipped", reason="oversight_tail_resume")
        db.commit()

    if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
        return False
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        run.current_step = "auto_timeline"
        _append_event(run, "auto_timeline", "running")
    db.commit()
    clips: list[dict[str, Any]] = []
    clip_order = 0
    proj_for_timeline = db.get(Project, pid)
    use_all_approved = bool(
        proj_for_timeline and getattr(proj_for_timeline, "use_all_approved_scene_media", False)
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
                    base_clip_sec=_scene_clip_duration_sec(settings),
                    storage_root=storage_root_tl,
                    ffprobe_bin=ffprobe_bin_tl,
                    timeout_sec=timeout_tl,
                    tail_padding_sec=_scene_vo_tail_padding_sec(settings),
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
                base_clip_sec=_scene_clip_duration_sec(settings),
                storage_root=storage_root_tl,
                ffprobe_bin=ffprobe_bin_tl,
                timeout_sec=timeout_tl,
                tail_padding_sec=_scene_vo_tail_padding_sec(settings),
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
            base_clip_sec=_scene_clip_duration_sec(settings),
            storage_root=storage_root_tl,
            ffprobe_bin=ffprobe_bin_tl,
            timeout_sec=timeout_tl,
            tail_padding_sec=_scene_vo_tail_padding_sec(settings),
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
        _append_event(run, "auto_timeline", "succeeded", timeline_version_id=str(tv_id))
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
        raise ValueError(format_phase5_readiness_failure(readiness, label="AUTO_ROUGH_NOT_READY"))

    if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
        return False
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        run.current_step = "auto_rough_cut"
        _append_event(run, "auto_rough_cut", "running")
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
    _rough_cut(db, rj, settings)
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        _append_event(run, "auto_rough_cut", "succeeded")
        db.commit()

    if _agent_run_checkpoint(db, agent_run_uuid) == "stop":
        return False
    db.refresh(tv)
    _attach_latest_music_bed_if_missing(
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
        _append_event(run, "auto_final_cut", "running")
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
    _final_cut(db, fj, settings)
    project = db.get(Project, pid)
    if project:
        project.workflow_phase = "final_video_ready"
    run = db.get(AgentRun, agent_run_uuid)
    if run:
        _append_event(run, "auto_final_cut", "succeeded", timeline_version_id=str(tv_id))
        db.commit()
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


@celery_app.task(
    name="director.run_agent_run",
    soft_time_limit=_CELERY_AGENT_RUN_SOFT_SEC,
    time_limit=_CELERY_AGENT_RUN_HARD_SEC,
)
def run_agent_run(agent_run_id: str) -> None:
    try:
        _run_agent_run_impl(agent_run_id)
    except AgentRunPausedYield:
        s = get_settings()
        countdown = float(getattr(s, "agent_run_pause_poll_sec", 2.0))
        celery_app.send_task(
            "director.run_agent_run",
            args=[agent_run_id],
            countdown=countdown,
        )
        log.info("agent_run_paused_requeued", agent_run_id=agent_run_id, countdown_sec=countdown)
        return
    try:
        from director_api.services.telegram_notify import telegram_notify_after_agent_run

        telegram_notify_after_agent_run(agent_run_id)
    except Exception as exc:
        log.warning("telegram_notify_after_run_failed", agent_run_id=agent_run_id, error=str(exc))


def _run_agent_run_impl(agent_run_id: str) -> None:
    aid = uuid.UUID(agent_run_id)
    with SessionLocal() as db:
        run = db.get(AgentRun, aid)
        if not run:
            log.error("agent_run_not_found", agent_run_id=agent_run_id)
            return
        settings = _worker_runtime_for_agent_run(db, run)
        if run.status in ("cancelled", "succeeded", "failed", "blocked"):
            log.info("agent_run_skip_terminal", agent_run_id=agent_run_id, status=run.status)
            return
        agent_run_just_started = False
        if _pipeline_control_dict(run.pipeline_control_json)["stop_requested"]:
            run.status = "cancelled"
            run.error_message = "Stopped by user"
            run.completed_at = datetime.now(timezone.utc)
            _append_event(run, "pipeline", "cancelled", reason="user_stop_before_start")
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
            _append_event(run, "director", "running")
            db.commit()
            agent_run_just_started = True

        def halt() -> bool:
            return _agent_run_checkpoint(db, aid) == "stop"

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
                _append_event(run, "director", "failed", error_code="PROJECT_MISSING")
                db.commit()
            return

        if project.tenant_id != run.tenant_id:
            run = db.get(AgentRun, aid)
            if run:
                run.status = "failed"
                run.error_message = "agent run tenant does not match project"
                run.completed_at = datetime.now(timezone.utc)
                _append_event(run, "director", "failed", error_code="TENANT_MISMATCH")
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
                    if _active_text_llm_configured(settings):
                        try:
                            snap = pipeline_oversight_svc.build_oversight_snapshot(
                                db, project, _root_storage if _root_storage.is_dir() else None
                            )
                            llm_gap, gaps_out, rationale = pipeline_oversight_svc.oversight_llm_advisory(
                                snap, settings=settings, usage_sink=usage_ov
                            )
                            _flush_llm_usage(db, project.tenant_id, project.id, None, None, usage_ov)
                        except Exception as e:  # noqa: BLE001
                            log.warning("oversight_llm_failed", error=str(e)[:500])
                    oversight_earliest = pipeline_oversight_svc.merge_earliest_steps(det_gap, llm_gap)
                    tail_resume = pipeline_oversight_svc.tail_resume_from_oversight(oversight_earliest)
                    run = db.get(AgentRun, aid)
                    if run:
                        _append_event(
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
                        _append_event(run, "rerun", "requested", from_step=rerun_from)
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
                                _require_active_text_llm(settings, for_what="chapter script generation")
                            except ValueError as e:
                                run = db.get(AgentRun, aid)
                                if run:
                                    _agent_run_mark_failed(db, run, "pipeline", e)
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
                        _append_event(run, "director", "skipped", reason="director_pack_present")
                        run.current_step = "research"
                        db.commit()
                else:
                    try:
                        if halt():
                            return
                        _ensure_director_pack(db, project, settings)
                        db.commit()
                        run = db.get(AgentRun, aid)
                        if run:
                            _append_event(run, "director", "succeeded")
                            run.current_step = "research"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _agent_run_mark_failed(db, run, "director", e)
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
                        _append_event(run, "research", "skipped", reason=_skip_reason)
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
                        _append_event(run, "research", "running")
                        db.commit()
                        project = db.get(Project, run_project_id)
                        if not project:
                            raise RuntimeError("project missing after director step")
                        _phase2_research_core(db, project, settings, agent_run_id=aid)
                        db.commit()
                        project = db.get(Project, run_project_id)
                        dossier = _latest_dossier(db, project.id) if project else None
                        if not dossier:
                            raise RuntimeError("research step did not create dossier")
                        _strict_research_gate(db, project, dossier, unattended=unattended)
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
                            _append_event(
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
                            _agent_run_mark_failed(db, run, "research", e)
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
                                _strict_research_gate(db, _proj_skip, _d_skip, unattended=unattended)
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
                                    _append_event(
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
                        dossier = _latest_dossier(db, project.id)
                        if dossier:
                            dossier.status = "approved"
                            dossier.approved_at = datetime.now(timezone.utc)
                            dossier.approved_notes = "Auto-approved after strict research gate (agent run)"
                        project.workflow_phase = "research_approved"
                        run = db.get(AgentRun, aid)
                        if run:
                            _append_event(run, "research", "succeeded", dossier_id=str(dossier.id) if dossier else None)
                            run.current_step = "outline"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _agent_run_mark_failed(db, run, "research_approve", e)
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
                        _append_event(run, "outline", "skipped", reason="outline_already_done")
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
                        _append_event(run, "outline", "running")
                        db.commit()
                        project = db.get(Project, run_project_id)
                        if not project:
                            raise RuntimeError("project missing before outline")
                        _phase2_outline_core(db, project, settings)
                        db.commit()
                        run = db.get(AgentRun, aid)
                        if run:
                            _append_event(run, "outline", "succeeded")
                            run.current_step = "chapters"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _agent_run_mark_failed(db, run, "outline", e)
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
                        _append_event(run, "chapters", "skipped", reason="scripts_already_done")
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
                        _append_event(run, "chapters", "running")
                        db.commit()
                        project = db.get(Project, run_project_id)
                        if not project:
                            raise RuntimeError("project missing before chapters")
                        _phase2_chapters_core(db, project, settings, preserve_substantive_scripts=cont)
                        db.commit()
                        run = db.get(AgentRun, aid)
                        if run:
                            _append_event(run, "chapters", "succeeded")
                            run.current_step = "scenes"
                            db.commit()
                    except Exception as e:  # noqa: BLE001
                        run = db.get(AgentRun, aid)
                        if run:
                            _agent_run_mark_failed(db, run, "chapters", e)
                        log.exception("agent_run_chapters_failed", agent_run_id=agent_run_id)
                        return

                if through == "chapters":
                    run = db.get(AgentRun, aid)
                    if run:
                        _append_event(
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
                        _append_event(run, "scenes", "skipped", reason="scenes_already_planned")
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
                        _append_event(run, "scenes", "running")
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

                        for plan_i, ch in enumerate(plan_queue):
                            if halt():
                                return
                            run = db.get(AgentRun, aid)
                            if run:
                                _append_event(
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
                            _phase3_scenes_plan_for_chapter(db, ch, project, settings)
                            planned += 1
                        db.commit()
                        run = db.get(AgentRun, aid)
                        if run:
                            _append_event(
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
                            _agent_run_mark_failed(db, run, "scenes", e)
                        log.exception("agent_run_scenes_failed", agent_run_id=agent_run_id)
                        return

                if halt():
                    return

                project = db.get(Project, run_project_id)
                # Story vs research: one automatic LLM pass per project after scenes, then never again (critic row is the latch).
                if project and _project_has_story_research_review_report(db, project.id):
                    run = db.get(AgentRun, aid)
                    if run:
                        _append_event(
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
                            _agent_run_mark_failed(db, run, "full_video", e)
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
                    _append_event(run, "story_research_review", "running")
                    db.commit()
                    project = db.get(Project, run_project_id)
                    if not project:
                        raise RuntimeError("project missing before story_research_review")

                    agent_meta: dict[str, Any] = {"source": "agent_run"}
                    fast = bool(settings.agent_run_fast)
                    no_key = not _active_text_llm_configured(settings)

                    if fast or no_key:
                        run = db.get(AgentRun, aid)
                        if run:
                            _append_event(
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
                        _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u_sr)
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
                            _append_event(
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
                            _agent_run_mark_failed(db, run, "full_video", e)
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
                        _agent_run_mark_failed(db, run, "story_research_review", e)
                    log.exception("agent_run_story_research_review_failed", agent_run_id=agent_run_id)
                    return


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
            cost_estimate=float(cost_estimate),
            credits=cr,
            meta_json=m,
        )
    )


def _phase3_scenes_plan_for_chapter(db, chapter: Chapter, project: Project, settings: Any) -> None:
    """Agentic scene planning (same as scene_generate job body)."""
    if not phase3_svc.chapter_eligible_for_scene_planning(chapter):
        raise ValueError(
            "chapter needs script_text or a substantive summary (12+ chars) before scene planning"
        )

    vis_prompt = effective_visual_style(project.visual_style, settings)
    char_prefix = character_consistency_prefix(db, project.id, max_chars=2000)
    try:
        min_sc = max(0, min(48, int(getattr(settings, "scene_plan_target_scenes_per_chapter", 0) or 0)))
    except (TypeError, ValueError):
        min_sc = 0
    try:
        clip_sec = int(getattr(settings, "scene_clip_duration_sec", None) or 10)
    except (TypeError, ValueError):
        clip_sec = 10
    if clip_sec not in (5, 10):
        clip_sec = 10
    seed_batch = phase3_svc.build_scene_plan_batch(
        chapter,
        project,
        visual_style_prompt=vis_prompt,
        min_scenes=min_sc,
        scene_clip_duration_sec=clip_sec,
        character_consistency_prefix=char_prefix or None,
    )
    batch = seed_batch
    refined = None
    llm_u: list[dict[str, Any]] = []
    char_ctx = character_bible_for_llm_context(db, project.id, max_chars=6000)
    plan_hints = phase3_svc.scene_plan_refine_context(chapter, settings)
    if not bool(getattr(settings, "agent_run_fast", False)):
        refined = phase3_llm.refine_scene_plan_batch(
            seed_batch,
            chapter_title=chapter.title,
            project_topic=project.topic,
            settings=settings,
            narration_style=effective_narration_style(
                project.narration_style, settings, db=db, tenant_id=project.tenant_id
            ),
            planning_hints=plan_hints,
            target_duration_sec=chapter.target_duration_sec,
            character_bible=char_ctx or None,
            frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
            usage_sink=llm_u,
        )
    _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
    if refined:
        try:
            validate_scene_plan_batch(refined)
            n_ref = len(refined.get("scenes") or [])
            n_seed = len(seed_batch.get("scenes") or [])
            collapsed = n_ref == 1 and n_seed > 1
            under_floor = min_sc > 0 and n_ref < min_sc
            wct = int(plan_hints.get("word_count") or 0)
            sugg = int(plan_hints.get("suggested_scene_count") or 1)
            # LLM often collapses to one scene; keep seed when hints call for 2+ beats (not only long scripts).
            ref_too_coarse = n_ref == 1 and sugg >= 2 and wct >= 18
            if not collapsed and not under_floor and not ref_too_coarse:
                batch = refined
        except Exception:
            pass
    validate_scene_plan_batch(batch)

    for sc in list(chapter.scenes):
        db.delete(sc)
    db.flush()

    for item in sorted(batch["scenes"], key=lambda x: int(x["order_index"])):
        pp = item.get("prompt_package_json")
        if not isinstance(pp, dict):
            pp = {}
        for pref_key in ("preferred_image_provider", "preferred_video_provider"):
            if item.get(pref_key):
                pp[f"_{pref_key}"] = str(item[pref_key])[:64]
        ct = item.get("continuity_tags_json")
        if not isinstance(ct, list):
            ct = []
        ct = [str(x)[:256] for x in ct if x is not None][:32]
        db.add(
            Scene(
                id=uuid.uuid4(),
                chapter_id=chapter.id,
                order_index=int(item["order_index"]),
                purpose=sanitize_jsonb_text(str(item["purpose"]), 2000),
                planned_duration_sec=int(item["planned_duration_sec"]),
                narration_text=sanitize_jsonb_text(str(item["narration_text"]), 12_000),
                visual_type=str(item["visual_type"])[:64],
                prompt_package_json=pp,
                continuity_tags_json=ct,
                status="planned",
            )
        )
    if agent_resume_svc.all_scripted_chapters_have_scenes(db, project):
        project.workflow_phase = "scenes_planned"
    else:
        project.workflow_phase = "chapters_ready"
    db.flush()


def _phase3_scenes_generate(db, job: Job) -> None:
    payload = job.payload or {}
    cid = uuid.UUID(str(payload["chapter_id"]))
    chapter = db.get(Chapter, cid)
    if not chapter:
        raise ValueError("chapter not found")
    project = db.get(Project, chapter.project_id)
    if not project:
        raise ValueError("project not found")
    if project.tenant_id != job.tenant_id:
        raise ValueError("job tenant does not match project")
    settings = _worker_runtime_for_job(db, job)
    _phase3_scenes_plan_for_chapter(db, chapter, project, settings)


def _phase3_scene_extend(db, job: Job) -> dict[str, Any]:
    """Append one scene after existing plans, using LLM (or deterministic seed when agent_run_fast)."""
    db.refresh(job)
    payload = job.payload if isinstance(job.payload, dict) else {}
    raw_cid = payload.get("chapter_id")
    if raw_cid is None:
        raise ValueError("scene_extend job missing chapter_id in payload")
    cid = uuid.UUID(str(raw_cid))
    chapter = db.get(Chapter, cid)
    if not chapter:
        log.error("scene_extend_chapter_missing", job_id=str(job.id), chapter_id=str(raw_cid))
        raise ValueError("chapter not found")
    if job.project_id and chapter.project_id != job.project_id:
        log.error(
            "scene_extend_chapter_project_mismatch",
            job_id=str(job.id),
            chapter_id=str(chapter.id),
            job_project_id=str(job.project_id),
            chapter_project_id=str(chapter.project_id),
        )
        raise ValueError("scene_extend job project_id does not match chapter.project_id")
    project = db.get(Project, chapter.project_id)
    if not project:
        raise ValueError("project not found")
    if not phase3_svc.chapter_eligible_for_scene_extend(chapter):
        raise ValueError(
            "chapter needs script_text, substantive summary, or enough text in existing scene beats "
            "before extending scenes"
        )
    if project.tenant_id != job.tenant_id:
        raise ValueError("job tenant does not match project")
    settings = _worker_runtime_for_job(db, job)
    scenes_sorted = sorted(list(chapter.scenes), key=lambda s: int(s.order_index or 0))
    if not scenes_sorted:
        raise ValueError("no existing scenes — run Plan scenes first")
    if len(scenes_sorted) >= 48:
        raise ValueError("chapter already has the maximum number of scenes (48)")
    next_idx = max(int(s.order_index or 0) for s in scenes_sorted) + 1

    vis_prompt = effective_visual_style(project.visual_style, settings)
    char_ctx = character_bible_for_llm_context(db, project.id, max_chars=6000)
    plan_hints = phase3_svc.scene_plan_refine_context(chapter, settings)
    try:
        clip = int(plan_hints.get("scene_clip_duration_sec") or 10)
    except (TypeError, ValueError):
        clip = 10
    if clip not in (5, 10):
        clip = 10

    existing: list[dict[str, Any]] = []
    for s in scenes_sorted[-4:]:
        ct = s.continuity_tags_json if isinstance(s.continuity_tags_json, list) else []
        existing.append(
            {
                "order_index": int(s.order_index or 0),
                "purpose": (s.purpose or "")[:400],
                "narration_text": (s.narration_text or "")[:2000],
                "visual_type": s.visual_type or "",
                "continuity_tags": [str(x)[:128] for x in ct[:8]],
            }
        )

    llm_u: list[dict[str, Any]] = []
    batch: dict[str, Any] | None = None
    if not bool(getattr(settings, "agent_run_fast", False)):
        batch = phase3_llm.extend_scene_plan_batch(
            existing,
            chapter_title=chapter.title or "",
            chapter_script=(chapter.script_text or ""),
            chapter_summary=(chapter.summary or ""),
            project_topic=project.topic or "",
            settings=settings,
            narration_style=effective_narration_style(
                project.narration_style, settings, db=db, tenant_id=project.tenant_id
            ),
            target_duration_sec=chapter.target_duration_sec,
            scene_clip_sec=clip,
            character_bible=char_ctx or None,
            frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
            usage_sink=llm_u,
        )
    if not batch:
        last = scenes_sorted[-1]
        narrs = [(x.narration_text or "") for x in scenes_sorted]
        batch = phase3_svc.build_extend_scene_deterministic(
            chapter,
            project,
            prior_narrations=narrs,
            last_visual_type=str(last.visual_type or ""),
            visual_style_prompt=vis_prompt,
            character_consistency_prefix=character_consistency_prefix(db, project.id, max_chars=2000) or None,
        )
    _flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
    validate_scene_plan_batch(batch)
    items = batch.get("scenes") or []
    if not items:
        raise ValueError("extend_scene produced no scenes")
    item = dict(items[0])
    item["order_index"] = next_idx

    pp = item.get("prompt_package_json")
    if not isinstance(pp, dict):
        pp = {}
    for pref_key in ("preferred_image_provider", "preferred_video_provider"):
        if item.get(pref_key):
            pp[f"_{pref_key}"] = str(item[pref_key])[:64]
    ct = item.get("continuity_tags_json")
    if not isinstance(ct, list):
        ct = []
    ct = [str(x)[:256] for x in ct if x is not None][:32]
    new_id = uuid.uuid4()
    db.add(
        Scene(
            id=new_id,
            chapter_id=chapter.id,
            order_index=int(item["order_index"]),
            purpose=sanitize_jsonb_text(str(item["purpose"]), 2000),
            planned_duration_sec=int(item["planned_duration_sec"]),
            narration_text=sanitize_jsonb_text(str(item["narration_text"]), 12_000),
            visual_type=str(item["visual_type"])[:64],
            prompt_package_json=pp,
            continuity_tags_json=ct,
            status="planned",
        )
    )
    if agent_resume_svc.all_scripted_chapters_have_scenes(db, project):
        project.workflow_phase = "scenes_planned"
    db.flush()
    log.info(
        "scene_extend_appended",
        chapter_id=str(chapter.id),
        scene_id=str(new_id),
        order_index=next_idx,
    )
    return {"scene_id": str(new_id)}


def _phase3_image_generate(db, job: Job) -> dict[str, Any]:
    settings = _worker_runtime_for_job(db, job)
    storage = FilesystemStorage(settings.local_storage_root)
    payload = job.payload or {}
    sid = uuid.UUID(str(payload["scene_id"]))
    tier = str(payload.get("generation_tier") or "preview")

    scene = db.get(Scene, sid)
    if not scene:
        raise ValueError("scene not found")
    chapter = db.get(Chapter, scene.chapter_id)
    if not chapter:
        raise ValueError("chapter not found")
    project = db.get(Project, chapter.project_id)
    if not project:
        raise ValueError("project not found")
    if project.tenant_id != job.tenant_id:
        raise ValueError("job tenant does not match project")
    tenant_id = str(payload.get("tenant_id") or project.tenant_id)
    exp_w, exp_h = _project_export_dimensions(project)

    ar_uuid = _payload_agent_run_uuid(payload)
    if ar_uuid is not None and _agent_run_checkpoint(db, ar_uuid) == "stop":
        return {"ok": False, "error_message": "Stopped by user", "stopped": True}

    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    override = payload.get("image_prompt_override")
    prompt, used_brackets, bracket_phrases = base_image_prompt_from_scene_fields(
        narration_text=scene.narration_text,
        prompt_package_json=pp,
        image_prompt_override=override if isinstance(override, str) else None,
    )
    bracket_llm_refined = False
    want_refine = bool(payload.get("refine_bracket_visual_with_llm"))
    if (
        bracket_phrases
        and want_refine
        and not (isinstance(override, str) and override.strip())
    ):
        refined, rerr = refine_bracket_visual_prompt_llm(
            db,
            settings,
            scene_id=scene.id,
            draft_prompt=prompt,
            bracket_phrases=bracket_phrases,
            narration_excerpt=scene.narration_text,
        )
        if refined:
            prompt = sanitize_jsonb_text(refined, 4000)
            bracket_llm_refined = True
        elif rerr:
            log.warning(
                "bracket_visual_llm_refine_failed",
                scene_id=str(scene.id),
                err=str(rerr)[:500],
            )

    bracket_visual_audit: dict[str, Any] | None = None
    if bracket_phrases:
        bracket_visual_audit = {
            "phrases": bracket_phrases,
            "refined_with_llm": bracket_llm_refined,
            "used_bracket_hints": used_brackets,
        }

    prefix = character_consistency_prefix(db, project.id, max_chars=2000)
    if prefix and not prompt_already_has_character_prefix(prompt, prefix):
        room = max(400, 4000 - len(prefix) - 3)
        prompt = f"{prefix}\n\n{str(prompt)[:room]}"

    vis_style = effective_visual_style(project.visual_style, settings)
    if vis_style:
        vs = vis_style.strip()
        if vs:
            tail = prompt[-min(len(prompt), 800) :] if prompt else ""
            if vs[:100] not in tail:
                room_vs = max(0, 4000 - len(prompt) - 24)
                if room_vs > 80:
                    prompt = f"{prompt}\n\nVisual style: {vs[:room_vs]}"

    scene_neg = _package_negative_prompt(pp)

    payload_override = payload.get("image_provider")
    if isinstance(payload_override, str) and payload_override.strip():
        requested = payload_override.strip()
    else:
        requested = (
            pp.get("_preferred_image_provider")
            or pp.get("preferred_image_provider")
            or project.preferred_image_provider
            or getattr(settings, "active_image_provider", None)
            or "fal"
        )
    req_l = str(requested).lower().strip()
    if req_l in ("auto", "default", ""):
        req_l = "fal"
    if req_l in ("openai", "grok", "xai", "gemini", "google"):
        req_l = "fal"
    if bool(getattr(settings, "director_placeholder_media", False)):
        req_l = "placeholder"

    if req_l == "placeholder":
        from director_api.providers.media_placeholder import render_placeholder_scene_png_bytes

        image_params_ph: dict[str, Any] = {
            "continuity_tags_json": scene.continuity_tags_json,
            "continuity_tags_summary": (scene.continuity_tags_json or [])
            if isinstance(scene.continuity_tags_json, list)
            else [],
            "prompt_package_json": scene.prompt_package_json,
            "image_prompt_used": prompt[:4000],
            "routing_audit": {"requested_provider": requested, "resolved_provider": "placeholder"},
        }
        if bracket_visual_audit:
            image_params_ph["bracket_visual"] = bracket_visual_audit
        asset_ph = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="image",
            status="running",
            generation_tier=tier,
            timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
            provider="placeholder",
            model_name="lavfi_color",
            params_json=image_params_ph,
        )
        db.add(asset_ph)
        db.flush()
        try:
            raw_png = render_placeholder_scene_png_bytes(
                ffmpeg_bin=(settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg",
                timeout_sec=min(float(settings.ffmpeg_timeout_sec), 120.0),
                width=exp_w,
                height=exp_h,
            )
            img_bytes, content_type, norm_trusted = _normalize_image_bytes_to_dims(
                settings, raw_png, "image/png", exp_w, exp_h
            )
            if not (norm_trusted or _image_bytes_magic_ok(img_bytes)):
                raise RuntimeError("placeholder PNG normalize failed or invalid magic")
            ext = "png" if "png" in content_type.lower() else "jpg"
            key = f"assets/{project.id}/{scene.id}/{asset_ph.id}.{ext}"
            url = storage.put_bytes(key, img_bytes)
            _bind_asset_local_file(asset_ph, url, key)
            asset_ph.status = "succeeded"
            asset_ph.error_message = None
            scene.status = "image_ready"
            _record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset_ph.id,
                provider="placeholder",
                service_type="image_gen",
                meta={"ok": True, "model": "placeholder", "tier": tier},
            )
        except Exception as exc:
            asset_ph.status = "failed"
            asset_ph.error_message = str(exc)[:8000]
            _record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset_ph.id,
                provider="placeholder",
                service_type="image_gen",
                meta={"ok": False, "error": str(exc)[:500], "tier": tier},
            )
        db.flush()
        out_ph: dict[str, Any] = {"asset_id": str(asset_ph.id), "ok": asset_ph.status == "succeeded"}
        if asset_ph.status != "succeeded" and asset_ph.error_message:
            out_ph["error_message"] = str(asset_ph.error_message)[:2000]
        return out_ph

    if req_l not in ("fal", "comfyui", "comfy"):
        failed_params: dict[str, Any] = {
            "continuity_tags_json": scene.continuity_tags_json,
            "continuity_tags_summary": scene.continuity_tags_json
            if isinstance(scene.continuity_tags_json, list)
            else [],
            "image_prompt_used": prompt[:2000],
            "routing_audit": {"requested_provider": requested, "resolved_provider": None},
        }
        if bracket_visual_audit:
            failed_params["bracket_visual"] = bracket_visual_audit
        asset = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="image",
            status="failed",
            generation_tier=tier,
            timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
            provider=str(requested)[:64],
            model_name=None,
            params_json=failed_params,
            error_message=(
                f"Image provider '{requested}' is not supported; use fal, ComfyUI, or placeholder "
                f"(see scripts/budget_pipeline_test.py / DIRECTOR_PLACEHOLDER_MEDIA)."
            ),
        )
        db.add(asset)
        db.flush()
        _record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider=str(requested)[:64],
            service_type="image_gen",
            meta={"ok": False, "skipped": True, "reason": "provider_not_supported", "tier": tier},
        )
        db.flush()
        return {
            "asset_id": str(asset.id),
            "ok": False,
            "error_message": asset.error_message or "provider_not_supported",
        }

    fal_image_override = payload.get("fal_image_model")
    if not isinstance(fal_image_override, str) or not fal_image_override.strip():
        fal_image_override = None
    else:
        fal_image_override = fal_image_override.strip().lstrip("/")

    resolved_provider = "fal"
    model_name = fal_image_override or settings.fal_smoke_model
    if req_l in ("comfyui", "comfy"):
        resolved_provider = "comfyui"
        wf = (settings.comfyui_workflow_json_path or "").strip()
        model_name = (settings.comfyui_model_name or "").strip() or (
            Path(wf).name if wf else "comfyui"
        )

    image_params: dict[str, Any] = {
        "continuity_tags_json": scene.continuity_tags_json,
        "continuity_tags_summary": (scene.continuity_tags_json or [])
        if isinstance(scene.continuity_tags_json, list)
        else [],
        "prompt_package_json": scene.prompt_package_json,
        "image_prompt_used": prompt[:4000],
        "routing_audit": {"requested_provider": requested, "resolved_provider": resolved_provider},
    }
    if bracket_visual_audit:
        image_params["bracket_visual"] = bracket_visual_audit
    if resolved_provider == "comfyui":
        image_params["comfyui_base_url"] = (settings.comfyui_base_url or "")[:256]
        image_params["comfyui_workflow_json_path"] = (settings.comfyui_workflow_json_path or "")[:512]
        image_params["comfyui_api_flavor"] = str(
            getattr(settings, "comfyui_api_flavor", "oss") or "oss"
        )[:32]

    asset = Asset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        scene_id=scene.id,
        project_id=project.id,
        asset_type="image",
        status="running",
        generation_tier=tier,
        timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
        provider=resolved_provider,
        model_name=model_name,
        params_json=image_params,
    )
    db.add(asset)
    db.flush()

    log.info(
        "phase3_image_dispatch",
        job_id=str(job.id),
        scene_id=str(scene.id),
        resolved_provider=resolved_provider,
        model_name=model_name,
        fal_key_configured=bool((settings.fal_key or "").strip()),
    )

    if resolved_provider == "comfyui":
        res = generate_scene_image_comfyui(settings, str(prompt), negative_prompt=scene_neg)
    else:
        res = generate_scene_image(
            settings,
            str(prompt),
            model_path=fal_image_override,
            negative_prompt=scene_neg,
            frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
        )

    if res.get("ok") and res.get("bytes"):
        content_type = str(res.get("content_type") or "image/png")
        img_bytes, content_type, norm_trusted = _normalize_image_bytes_to_dims(
            settings, res["bytes"], content_type, exp_w, exp_h
        )
        if not (norm_trusted or _image_bytes_magic_ok(img_bytes)):
            asset.status = "failed"
            asset.error_message = (
                "Image bytes were empty or not a recognized image format after generation/normalize "
                "(check fal model output and ffmpeg image step)."
            )[:8000]
            _record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset.id,
                provider=str(res.get("provider") or resolved_provider),
                service_type="image_gen",
                meta={"ok": False, "error": "invalid_image_bytes", "tier": tier},
            )
        else:
            ext = "png" if "png" in content_type.lower() else "jpg"
            key = f"assets/{project.id}/{scene.id}/{asset.id}.{ext}"
            url = storage.put_bytes(key, img_bytes)
            _bind_asset_local_file(asset, url, key)
            asset.status = "succeeded"
            asset.error_message = None
            scene.status = "image_ready"
            _record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset.id,
                provider=str(res.get("provider") or resolved_provider),
                service_type="image_gen",
                meta={"ok": True, "model": str(res.get("model") or model_name), "tier": tier},
            )
    else:
        asset.status = "failed"
        err = format_fal_result_message(res)
        asset.error_message = err
        _record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider=str(res.get("provider") or "fal"),
            service_type="image_gen",
            meta={"ok": False, "error": err[:500], "tier": tier},
        )
    db.flush()
    out: dict[str, Any] = {"asset_id": str(asset.id), "ok": asset.status == "succeeded"}
    if asset.status != "succeeded" and asset.error_message:
        out["error_message"] = str(asset.error_message)[:2000]
    return out


def _phase3_video_generate(db, job: Job) -> dict[str, Any]:
    """Encode a still frame from the latest succeeded scene image to MP4 (local FFmpeg + storage)."""
    settings = _worker_runtime_for_job(db, job)
    payload = job.payload or {}
    sid = uuid.UUID(str(payload["scene_id"]))
    tier = str(payload.get("generation_tier") or "preview")
    notes = payload.get("notes")

    scene = db.get(Scene, sid)
    if not scene:
        raise ValueError("scene not found")
    chapter = db.get(Chapter, scene.chapter_id)
    if not chapter:
        raise ValueError("chapter not found")
    project = db.get(Project, chapter.project_id)
    if not project:
        raise ValueError("project not found")
    if project.tenant_id != job.tenant_id:
        raise ValueError("job tenant does not match project")
    tenant_id = str(payload.get("tenant_id") or project.tenant_id)
    exp_w, exp_h = _project_export_dimensions(project)

    ar_uuid = _payload_agent_run_uuid(payload)
    if ar_uuid is not None and _agent_run_checkpoint(db, ar_uuid) == "stop":
        return {"ok": False, "error_message": "Stopped by user", "stopped": True}

    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    base_video_text_prompt = _resolve_phase3_video_text_prompt(
        scene, pp, override=payload.get("video_prompt_override")
    )
    payload_override = payload.get("video_provider")
    if isinstance(payload_override, str) and payload_override.strip():
        requested = payload_override.strip()
    else:
        requested = (
            pp.get("_preferred_video_provider")
            or pp.get("preferred_video_provider")
            or project.preferred_video_provider
            or getattr(settings, "active_video_provider", None)
            or "fal"
        )
    selected_video_provider = str(requested).strip().lower()
    if selected_video_provider in ("auto", "default", ""):
        selected_video_provider = "fal"
    if selected_video_provider in ("grok", "xai", "gemini", "google", "local_ltx", "local_wan"):
        selected_video_provider = "fal"
    if bool(getattr(settings, "director_placeholder_media", False)):
        if selected_video_provider in ("fal", "comfyui_wan"):
            selected_video_provider = "local_ffmpeg"
    if selected_video_provider not in (
        "local_ffmpeg",
        "fal",
        "comfyui_wan",
    ):
        raise ValueError(
            f"Video provider '{selected_video_provider}' is not supported; use local_ffmpeg (still→MP4), fal, or comfyui_wan."
        )

    if selected_video_provider in ("fal", "comfyui_wan"):
        prompt = base_video_text_prompt
        vprefix = character_consistency_prefix(db, project.id, max_chars=2000)
        if vprefix and not prompt_already_has_character_prefix(prompt, vprefix):
            room = max(400, 3000 - len(vprefix) - 3)
            prompt = f"{vprefix}\n\n{str(prompt)[:room]}"
        vis_style = effective_visual_style(project.visual_style, settings)
        if vis_style:
            room_vs = max(0, 4000 - len(prompt) - 24)
            if room_vs > 80:
                prompt = f"{prompt}\n\nVisual style: {vis_style.strip()[:room_vs]}"
        duration_sec = max(1.0, min(_scene_clip_duration_sec(settings), 30.0))
        fal_video_override = payload.get("fal_video_model")
        if not isinstance(fal_video_override, str) or not fal_video_override.strip():
            fal_video_override = None
        else:
            fal_video_override = fal_video_override.strip().lstrip("/")

        scene_comfy_path: Path | None = None
        prereq_image_asset_id: str | None = None
        if selected_video_provider == "comfyui_wan" and settings.comfyui_video_use_scene_image:
            storage_root_c = Path(settings.local_storage_root).resolve()
            imgs_c = list(
                db.scalars(
                    select(Asset)
                    .where(
                        Asset.scene_id == sid,
                        Asset.asset_type == "image",
                        Asset.status == "succeeded",
                        Asset.storage_url.is_not(None),
                    )
                    .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
                ).all()
            )
            approved_c = [a for a in imgs_c if a.approved_at is not None]
            pick_c = approved_c if approved_c else imgs_c
            if not pick_c:
                wf_still = (settings.comfyui_workflow_json_path or "").strip()
                if not wf_still:
                    fail = Asset(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        scene_id=scene.id,
                        project_id=project.id,
                        asset_type="video",
                        status="failed",
                        generation_tier=tier,
                        timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
                        provider="comfyui_wan",
                        model_name=(settings.comfyui_video_model_name or "wan-2.1-comfyui").strip(),
                        params_json={
                            "routing_audit": {"requested_provider": requested, "resolved_provider": "comfyui_wan"},
                            "notes": str(notes)[:2000] if notes else None,
                        },
                        error_message=(
                            "comfyui_wan: no scene image and COMFYUI_WORKFLOW_JSON_PATH is empty — set it to your "
                            "Flux/still API JSON so Directely can generate a still before WAN video, or add a scene image."
                        ),
                    )
                    db.add(fail)
                    db.flush()
                    _record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=fail.id,
                        provider="comfyui_wan",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "no_still_workflow", "tier": tier},
                    )
                    db.flush()
                    return {"asset_id": str(fail.id)}
                img_prompt = _scene_still_prompt_for_comfy(db, scene, project, settings)
                log.info(
                    "comfyui_wan_auto_still",
                    scene_id=str(scene.id),
                    workflow_hint=wf_still[:120],
                )
                ires = generate_scene_image_comfyui(
                    settings, img_prompt, negative_prompt=_package_negative_prompt(scene.prompt_package_json)
                )
                if not ires.get("ok") or not ires.get("bytes"):
                    err = str(ires.get("detail") or ires.get("error") or "comfyui_still_failed")[:8000]
                    fail = Asset(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        scene_id=scene.id,
                        project_id=project.id,
                        asset_type="video",
                        status="failed",
                        generation_tier=tier,
                        timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
                        provider="comfyui_wan",
                        model_name=(settings.comfyui_video_model_name or "wan-2.1-comfyui").strip(),
                        params_json={
                            "routing_audit": {"requested_provider": requested, "resolved_provider": "comfyui_wan"},
                            "notes": str(notes)[:2000] if notes else None,
                            "prereq": "comfyui_still_for_video",
                            "comfyui_workflow_json_path": wf_still[:512],
                        },
                        error_message=f"comfyui_wan: auto still (ComfyUI) failed: {err[:2000]}",
                    )
                    db.add(fail)
                    db.flush()
                    _record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=fail.id,
                        provider="comfyui_wan",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "auto_still_failed", "tier": tier},
                    )
                    db.flush()
                    return {"asset_id": str(fail.id)}
                storage = FilesystemStorage(settings.local_storage_root)
                img_wf_name = (settings.comfyui_model_name or "").strip() or Path(wf_still).name
                img_asset = Asset(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    scene_id=scene.id,
                    project_id=project.id,
                    asset_type="image",
                    status="running",
                    generation_tier=tier,
                    timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
                    provider="comfyui",
                    model_name=img_wf_name,
                    params_json={
                        "continuity_tags_json": scene.continuity_tags_json,
                        "continuity_tags_summary": (scene.continuity_tags_json or [])
                        if isinstance(scene.continuity_tags_json, list)
                        else [],
                        "prompt_package_json": scene.prompt_package_json,
                        "image_prompt_used": img_prompt[:4000],
                        "routing_audit": {
                            "requested_provider": "comfyui_wan_prereq",
                            "resolved_provider": "comfyui",
                        },
                        "auto_generated_for_comfyui_wan_video": True,
                        "comfyui_base_url": (settings.comfyui_base_url or "")[:256],
                        "comfyui_workflow_json_path": wf_still[:512],
                    },
                )
                db.add(img_asset)
                db.flush()
                ct = str(ires.get("content_type") or "image/png")
                img_bytes, ct, norm_trusted = _normalize_image_bytes_to_dims(
                    settings, ires["bytes"], ct, exp_w, exp_h
                )
                if not (norm_trusted or _image_bytes_magic_ok(img_bytes)):
                    img_asset.status = "failed"
                    img_asset.error_message = (
                        "Image bytes were empty or not a recognized image format after generation/normalize "
                        "(check fal model output and ffmpeg image step)."
                    )[:8000]
                    db.flush()
                    fail = Asset(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        scene_id=scene.id,
                        project_id=project.id,
                        asset_type="video",
                        status="failed",
                        generation_tier=tier,
                        timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
                        provider="comfyui_wan",
                        model_name=(settings.comfyui_video_model_name or "wan-2.1-comfyui").strip(),
                        params_json={
                            "routing_audit": {"requested_provider": requested, "resolved_provider": "comfyui_wan"},
                            "notes": str(notes)[:2000] if notes else None,
                            "prereq": "comfyui_still_for_video",
                            "comfyui_workflow_json_path": wf_still[:512],
                        },
                        error_message=(
                            "comfyui_wan: auto still bytes invalid after normalize (empty or unrecognized format)."
                        ),
                    )
                    db.add(fail)
                    db.flush()
                    _record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=fail.id,
                        provider="comfyui_wan",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "auto_still_invalid_bytes", "tier": tier},
                    )
                    db.flush()
                    return {"asset_id": str(fail.id)}
                ext = "png" if "png" in ct.lower() else "jpg"
                ikey = f"assets/{project.id}/{scene.id}/{img_asset.id}.{ext}"
                iurl = storage.put_bytes(ikey, img_bytes)
                _bind_asset_local_file(img_asset, iurl, ikey)
                img_asset.status = "succeeded"
                img_asset.error_message = None
                scene.status = "image_ready"
                _record_usage(
                    db,
                    tenant_id=tenant_id,
                    project_id=project.id,
                    scene_id=scene.id,
                    asset_id=img_asset.id,
                    provider=str(ires.get("provider") or "comfyui"),
                    service_type="image_gen",
                    meta={
                        "ok": True,
                        "model": str(ires.get("model") or img_wf_name),
                        "tier": tier,
                        "prereq": "comfyui_wan_video",
                    },
                )
                db.flush()
                prereq_image_asset_id = str(img_asset.id)
                scene_comfy_path = path_from_storage_url(img_asset.storage_url, storage_root=storage_root_c)
                if scene_comfy_path is None or not path_is_readable_file(scene_comfy_path):
                    raise ValueError(
                        "auto-generated ComfyUI still saved but file not found under LOCAL_STORAGE_ROOT"
                    )
            else:
                ip_c = path_from_storage_url(pick_c[0].storage_url, storage_root=storage_root_c)
                if ip_c is None or not path_is_readable_file(ip_c):
                    raise ValueError(
                        "scene image missing on local storage for comfyui_wan (file:// under LOCAL_STORAGE_ROOT)"
                    )
                scene_comfy_path = ip_c

        if selected_video_provider == "fal":
            resolved_provider = "fal"
            model_name = fal_video_override or settings.fal_video_model
        else:
            resolved_provider = "comfyui_wan"
            model_name = (settings.comfyui_video_model_name or "wan-2.1-comfyui").strip() or "wan-2.1-comfyui"
        vparams: dict[str, Any] = {
            "prompt_used": prompt,
            "video_prompt_base": base_video_text_prompt[:3000],
            "planned_duration_sec": duration_sec,
            "duration_source": "runtime_setting:scene_clip_duration_sec",
            "routing_audit": {
                "requested_provider": requested,
                "resolved_provider": resolved_provider,
            },
            "notes": str(notes)[:2000] if notes else None,
        }
        if resolved_provider == "comfyui_wan":
            vparams["comfyui_base_url"] = (settings.comfyui_base_url or "")[:256]
            vparams["comfyui_video_workflow_json_path"] = (settings.comfyui_video_workflow_json_path or "")[:512]
            vparams["comfyui_video_use_scene_image"] = bool(settings.comfyui_video_use_scene_image)
            vparams["comfyui_api_flavor"] = str(
                getattr(settings, "comfyui_api_flavor", "oss") or "oss"
            )[:32]
            if prereq_image_asset_id:
                vparams["prereq_image_asset_id"] = prereq_image_asset_id
                vparams["prereq_still_auto_generated"] = True
        asset = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="video",
            status="running",
            generation_tier=tier,
            timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
            provider=resolved_provider,
            model_name=model_name,
            params_json=vparams,
        )
        db.add(asset)
        db.flush()
        log.info(
            "phase3_video_dispatch",
            job_id=str(job.id),
            scene_id=str(scene.id),
            resolved_provider=resolved_provider,
            model_name=model_name,
            fal_key_configured=bool((settings.fal_key or "").strip()),
        )
        if resolved_provider == "fal":
            vm_path = fal_video_override or settings.fal_video_model
            scene_image_bytes: bytes | None = None
            scene_image_ct: str | None = None
            if fal_model_is_image_to_video(vm_path):
                storage_root_f = Path(settings.local_storage_root).resolve()
                imgs_f = list(
                    db.scalars(
                        select(Asset)
                        .where(
                            Asset.scene_id == sid,
                            Asset.asset_type == "image",
                            Asset.status == "succeeded",
                            Asset.storage_url.is_not(None),
                        )
                        .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
                    ).all()
                )
                approved_f = [a for a in imgs_f if a.approved_at is not None]
                pick_f = approved_f if approved_f else imgs_f
                if not pick_f:
                    asset.status = "failed"
                    asset.error_message = (
                        "fal image-to-video requires a scene image first — generate an image for this scene, "
                        "then run video again."
                    )[:8000]
                    db.flush()
                    _record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=asset.id,
                        provider="fal",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "i2v_no_scene_image", "tier": tier},
                    )
                    db.flush()
                    return {"asset_id": str(asset.id)}
                ip_f = path_from_storage_url(pick_f[0].storage_url, storage_root=storage_root_f)
                if ip_f is None or not path_is_readable_file(ip_f):
                    asset.status = "failed"
                    asset.error_message = (
                        "Scene image file missing under LOCAL_STORAGE_ROOT (cannot run fal image-to-video)."
                    )[:8000]
                    db.flush()
                    _record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=asset.id,
                        provider="fal",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "i2v_image_not_on_disk", "tier": tier},
                    )
                    db.flush()
                    return {"asset_id": str(asset.id)}
                scene_image_bytes = ip_f.read_bytes()
                suf = ip_f.suffix.lower()
                if suf == ".png":
                    scene_image_ct = "image/png"
                elif suf in (".jpg", ".jpeg"):
                    scene_image_ct = "image/jpeg"
                elif suf == ".webp":
                    scene_image_ct = "image/webp"
            vres = generate_scene_video_fal(
                settings,
                prompt,
                duration_sec,
                model=fal_video_override,
                image_bytes=scene_image_bytes,
                image_content_type=scene_image_ct,
                frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
            )
        else:
            vres = generate_scene_video_comfyui(
                settings,
                prompt,
                scene_image_path=scene_comfy_path,
                duration_sec=duration_sec,
            )
        if vres.get("ok") and vres.get("bytes"):
            storage = FilesystemStorage(settings.local_storage_root)
            key = f"assets/{project.id}/{scene.id}/{asset.id}.mp4"
            vbytes = _normalize_video_bytes_to_dims(settings, vres["bytes"], exp_w, exp_h)
            url = storage.put_bytes(key, vbytes)
            _bind_asset_local_file(asset, url, key)
            asset.status = "succeeded"
            asset.error_message = None
            _record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset.id,
                provider=str(vres.get("provider") or resolved_provider),
                service_type="video_gen",
                meta={"ok": True, "model": str(vres.get("model") or model_name), "tier": tier},
            )
        else:
            err = format_fal_result_message(vres)
            asset.status = "failed"
            asset.error_message = err
            _record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset.id,
                provider=str(vres.get("provider") or resolved_provider),
                service_type="video_gen",
                meta={"ok": False, "error": err[:500], "tier": tier},
            )
        db.flush()
        return {"asset_id": str(asset.id)}

    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        asset = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="video",
            status="failed",
            generation_tier=tier,
            timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
            provider="local_ffmpeg",
            model_name="still_to_mp4",
            params_json={
                "source_image_asset_id": None,
                "routing_audit": {"requested_provider": requested, "resolved_provider": "local_ffmpeg"},
                "notes": str(notes)[:2000] if notes else None,
            },
            error_message="ffmpeg binary not found on worker PATH (required for local still→video encode)",
        )
        db.add(asset)
        db.flush()
        _record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider="local_ffmpeg",
            service_type="video_gen",
            meta={"ok": False, "reason": "ffmpeg_missing", "tier": tier},
        )
        db.flush()
        return {"asset_id": str(asset.id)}

    imgs = list(
        db.scalars(
            select(Asset)
            .where(
                Asset.scene_id == sid,
                Asset.asset_type == "image",
                Asset.status == "succeeded",
                Asset.storage_url.is_not(None),
            )
            .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
        ).all()
    )
    approved_imgs = [a for a in imgs if a.approved_at is not None]
    pick_imgs = approved_imgs if approved_imgs else imgs
    if not pick_imgs:
        asset = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="video",
            status="failed",
            generation_tier=tier,
            timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
            provider="local_ffmpeg",
            model_name="still_to_mp4",
            params_json={
                "routing_audit": {"requested_provider": requested, "resolved_provider": "local_ffmpeg"},
                "notes": str(notes)[:2000] if notes else None,
            },
            error_message="no succeeded image with storage for this scene; run generate-image first",
        )
        db.add(asset)
        db.flush()
        _record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider="local_ffmpeg",
            service_type="video_gen",
            meta={"ok": False, "reason": "no_source_image", "tier": tier},
        )
        db.flush()
        return {"asset_id": str(asset.id)}

    storage_root = Path(settings.local_storage_root).resolve()
    resolved_paths: list[tuple[Asset, Path]] = []
    for img in pick_imgs:
        ip = path_from_storage_url(img.storage_url, storage_root=storage_root)
        if ip is None or not path_is_readable_file(ip):
            raise ValueError(
                "source image missing on local storage (file:// expected under LOCAL_STORAGE_ROOT)"
                f" for asset {img.id}"
            )
        resolved_paths.append((img, ip))

    duration_sec = max(0.5, min(_scene_clip_duration_sec(settings), 300.0))
    src_image = pick_imgs[0]
    use_slideshow = len(resolved_paths) > 1
    model_name = "image_slideshow_mp4" if use_slideshow else "still_to_mp4"
    per_slide_sec = duration_sec / len(resolved_paths) if use_slideshow else duration_sec
    slow_zoom_ff, kb_dir, slide_motion = _local_ffmpeg_motion_from_video_prompt(base_video_text_prompt)

    params_json: dict[str, Any] = {
        "continuity_tags_json": scene.continuity_tags_json,
        "continuity_tags_summary": (scene.continuity_tags_json or [])
        if isinstance(scene.continuity_tags_json, list)
        else [],
        "prompt_package_json": scene.prompt_package_json,
        "source_image_asset_id": str(src_image.id),
        "source_image_asset_ids": [str(a.id) for a in pick_imgs],
        "planned_duration_sec": duration_sec,
        "duration_source": "runtime_setting:scene_clip_duration_sec",
        "routing_audit": {
            "requested_provider": requested,
            "resolved_provider": "local_ffmpeg",
        },
        "notes": str(notes)[:2000] if notes else None,
        "video_prompt_resolved": base_video_text_prompt[:3000],
        "ffmpeg_motion_hint": slide_motion,
        "slow_zoom": slow_zoom_ff,
        "ken_burns_direction": kb_dir,
    }
    if use_slideshow:
        params_json["slide_count"] = len(resolved_paths)
        params_json["per_slide_duration_sec"] = per_slide_sec

    asset = Asset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        scene_id=scene.id,
        project_id=project.id,
        asset_type="video",
        status="running",
        generation_tier=tier,
        timeline_sequence=_next_timeline_sequence_for_scene(db, scene.id),
        provider="local_ffmpeg",
        model_name=model_name,
        params_json=params_json,
    )
    db.add(asset)
    db.flush()

    log.info(
        "phase3_video_dispatch",
        job_id=str(job.id),
        scene_id=str(scene.id),
        resolved_provider="local_ffmpeg",
        model_name=model_name,
        slide_count=len(resolved_paths),
    )

    storage = FilesystemStorage(str(storage_root))
    key = f"assets/{project.id}/{scene.id}/{asset.id}.mp4"
    out_path = storage.get_path(key)

    try:
        w = exp_w
        h = exp_h
        tmo = float(settings.ffmpeg_timeout_sec)
        if use_slideshow:
            slides = [(p, per_slide_sec) for _a, p in resolved_paths]
            sm = slide_motion if slide_motion in ("pan", "zoom") else "none"
            enc = compile_image_slideshow(
                slides,
                out_path,
                width=w,
                height=h,
                fps=30,
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=tmo,
                motion=sm,
                crossfade_sec=0.0,
                slow_zoom=False,
            )
        else:
            enc = encode_image_to_mp4(
                resolved_paths[0][1],
                out_path,
                duration_sec=duration_sec,
                width=w,
                height=h,
                slow_zoom=slow_zoom_ff,
                ken_burns_direction=kb_dir if kb_dir in ("in", "out") else "in",
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=tmo,
            )
    except FFmpegCompileError as e:
        err = str(e)[:8000]
        asset.status = "failed"
        asset.error_message = err
        _record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider="local_ffmpeg",
            service_type="video_gen",
            meta={"ok": False, "error": err[:500], "tier": tier},
        )
        db.flush()
        return {"asset_id": str(asset.id)}

    url = out_path.resolve().as_uri()
    _bind_asset_local_file(asset, url, key)
    asset.status = "succeeded"
    asset.error_message = None
    _record_usage(
        db,
        tenant_id=tenant_id,
        project_id=project.id,
        scene_id=scene.id,
        asset_id=asset.id,
        provider="local_ffmpeg",
        service_type="video_gen",
        meta={
            "ok": True,
            "tier": tier,
            "duration_sec": float(duration_sec),
            **{k: v for k, v in enc.items() if k != "output_path"},
        },
    )
    db.flush()
    return {"asset_id": str(asset.id)}


# Scene planning hits the text LLM with large JSON; local models (e.g. Qwen via LM Studio) may need >10 min/chapter.
@celery_app.task(
    bind=True,
    name="director.run_phase3_job",
    soft_time_limit=_CELERY_PHASE3_SOFT_SEC,
    time_limit=_CELERY_PHASE3_HARD_SEC,
)
def run_phase3_job(self, job_id: str) -> None:
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


def _revision_issue_scene_id(refs: Any, fallback: uuid.UUID | None) -> uuid.UUID | None:
    if not isinstance(refs, dict):
        return fallback
    ids = refs.get("scene_ids")
    if isinstance(ids, list) and ids:
        try:
            return uuid.UUID(str(ids[0]))
        except (ValueError, TypeError):
            return fallback
    return fallback


def _persist_revision_issues(
    db,
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    issues: list[dict[str, Any]],
    default_scene_id: uuid.UUID | None,
) -> None:
    for it in issues:
        refs = it.get("refs")
        sid = _revision_issue_scene_id(refs, default_scene_id)
        db.add(
            RevisionIssue(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                critic_report_id=report_id,
                scene_id=sid,
                asset_id=None,
                code=str(it.get("code") or "GENERIC")[:64],
                severity=str(it.get("severity") or "medium")[:16],
                message=str(it.get("message") or "")[:8000],
                refs_json=refs if isinstance(refs, (dict, list)) else None,
                status="open",
            )
        )


def _phase4_scene_critique_core(
    db,
    *,
    scene_id: uuid.UUID,
    tenant_id: str,
    job_id: uuid.UUID | None,
    prior_report_id_in: uuid.UUID | None,
    settings: Any,
    meta_extra: dict[str, Any] | None = None,
    prefetched_llm: tuple[dict[str, Any] | None, list[str] | None] | None = None,
) -> dict[str, Any]:
    """Scene critic — used by Celery job and autonomous agent run."""
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
    if int(sc.critic_revision_count or 0) >= pol.max_revision_cycles_per_scene:
        raise ValueError("critic_revision_cap_exceeded")

    cont = phase4_svc.continuity_findings_for_scene(
        sc,
        list(db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()),
    )
    assets = list(db.scalars(select(Asset).where(Asset.scene_id == sc.id)).all())
    has_ok_image = any(a.asset_type == "image" and a.approved_at is not None for a in assets)

    baseline: float | None = None
    prior_report_id: uuid.UUID | None = None
    if prior_report_id_in:
        prior = db.get(CriticReport, prior_report_id_in)
        if prior and prior.target_type == "scene" and prior.target_id == sc.id:
            baseline = prior.score
            prior_report_id = prior.id
    if baseline is None and sc.critic_score is not None:
        baseline = float(sc.critic_score)

    llm_payload = phase4_svc.build_scene_critique_llm_payload(db, sc)
    llm_u: list[dict[str, Any]] = []
    if prefetched_llm is None:
        llm_dims, llm_recs = phase4_llm.critique_scene_llm(
            llm_payload, settings=settings, usage_sink=llm_u
        )
    else:
        llm_dims, llm_recs = prefetched_llm
    _flush_llm_usage(db, tenant_id, project.id, sc.id, None, llm_u)

    score, passed, dims, issues, recs = phase4_svc.merge_heuristic_scene_critique(
        continuity_issues=cont,
        has_approved_image=has_ok_image,
        dimensions_llm=llm_dims,
        recommendations_llm=llm_recs,
        threshold=pol.pass_threshold,
        missing_dimension_default=pol.missing_dimension_default,
        dimension_invalid_fallback=pol.dimension_invalid_fallback,
    )

    meta = {
        "revision_index": int(sc.critic_revision_count or 0) + 1,
        "threshold": pol.pass_threshold,
    }
    if meta_extra:
        meta.update(meta_extra)

    rep = CriticReport(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=project.id,
        target_type="scene",
        target_id=sc.id,
        job_id=job_id,
        score=score,
        passed=passed,
        dimensions_json=dims,
        issues_json=issues,
        recommendations_json=recs,
        continuity_json={"findings": cont},
        baseline_score=baseline,
        prior_report_id=prior_report_id,
        meta_json=meta,
    )
    db.add(rep)
    db.flush()
    _persist_revision_issues(
        db,
        tenant_id=tenant_id,
        project_id=project.id,
        report_id=rep.id,
        issues=issues,
        default_scene_id=sc.id,
    )

    sc.critic_score = score
    sc.critic_passed = passed
    sc.critic_revision_count = int(sc.critic_revision_count or 0) + 1

    return {"critic_report_id": str(rep.id), "score": score, "passed": passed}


def _phase4_scene_critique(db, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    praw = payload.get("prior_report_id")
    return _phase4_scene_critique_core(
        db,
        scene_id=uuid.UUID(str(payload["scene_id"])),
        tenant_id=str(payload.get("tenant_id") or settings.default_tenant_id),
        job_id=job.id,
        prior_report_id_in=uuid.UUID(str(praw)) if praw else None,
        settings=settings,
        meta_extra=None,
    )


def _phase4_chapter_critique_core(
    db,
    *,
    chapter_id: uuid.UUID,
    tenant_id: str,
    job_id: uuid.UUID | None,
    settings: Any,
    meta_extra: dict[str, Any] | None = None,
    prefetched_llm: tuple[dict[str, Any] | None, list[str] | None] | None = None,
) -> dict[str, Any]:
    ch = db.get(Chapter, chapter_id)
    if not ch:
        raise ValueError("chapter not found")
    project = db.get(Project, ch.project_id)
    if not project or project.tenant_id != tenant_id:
        raise ValueError("project not found")

    scenes = list(
        db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
    )
    rollup = phase4_svc.chapter_continuity_rollup(scenes)
    llm_payload = phase4_svc.build_chapter_critique_llm_payload(db, ch)
    llm_u: list[dict[str, Any]] = []
    if prefetched_llm is None:
        llm_dims, llm_recs = phase4_llm.critique_chapter_llm(
            llm_payload, settings=settings, usage_sink=llm_u
        )
    else:
        llm_dims, llm_recs = prefetched_llm
    _flush_llm_usage(db, tenant_id, project.id, None, None, llm_u)

    pol = critic_policy_svc.effective_policy(project, settings)
    score, passed, dims, issues, recs = phase4_svc.chapter_aggregate_from_scenes(
        scenes,
        target_duration_sec=ch.target_duration_sec,
        chapter_dims_llm=llm_dims,
        continuity_rollup=rollup,
        threshold_ratio=pol.chapter_min_scene_pass_ratio,
        min_aggregate_score=pol.chapter_pass_score_threshold,
        missing_dimension_default=pol.missing_dimension_default,
        dimension_invalid_fallback=pol.dimension_invalid_fallback,
    )
    if llm_recs:
        recs = list(dict.fromkeys(recs + llm_recs))[:20]

    meta = {
        "threshold_scene_pass_ratio": pol.chapter_min_scene_pass_ratio,
        "threshold_chapter_aggregate_score": pol.chapter_pass_score_threshold,
    }
    if meta_extra:
        meta.update(meta_extra)

    rep = CriticReport(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=project.id,
        target_type="chapter",
        target_id=ch.id,
        job_id=job_id,
        score=score,
        passed=passed,
        dimensions_json=dims,
        issues_json=issues,
        recommendations_json=recs,
        continuity_json=rollup,
        baseline_score=None,
        prior_report_id=None,
        meta_json=meta,
    )
    db.add(rep)
    db.flush()
    _persist_revision_issues(
        db,
        tenant_id=tenant_id,
        project_id=project.id,
        report_id=rep.id,
        issues=issues,
        default_scene_id=None,
    )

    ch.critic_gate_status = "passed" if passed else "blocked"

    return {"critic_report_id": str(rep.id), "score": score, "passed": passed}


def _phase4_chapter_critique(db, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    return _phase4_chapter_critique_core(
        db,
        chapter_id=uuid.UUID(str(payload["chapter_id"])),
        tenant_id=str(payload.get("tenant_id") or settings.default_tenant_id),
        job_id=job.id,
        settings=settings,
        meta_extra=None,
    )


def _scene_critic_revision_apply_from_latest_report(
    db,
    sc: Scene,
    project: Project,
    settings: Any,
) -> tuple[str, uuid.UUID | None]:
    """
    Rewrite scene narration from latest scene critic report (same logic as scene_critic_revision job).
    Returns (mode, prior_report_id).
    """
    report = db.scalars(
        select(CriticReport)
        .where(CriticReport.target_type == "scene", CriticReport.target_id == sc.id)
        .order_by(desc(CriticReport.created_at))
        .limit(1)
    ).first()
    if not report:
        return "skip", None

    recs: list[str] = []
    if isinstance(report.recommendations_json, list):
        recs = [str(x) for x in report.recommendations_json if x is not None][:20]
    if not recs and isinstance(report.issues_json, list):
        for it in report.issues_json[:12]:
            if isinstance(it, dict) and it.get("message"):
                recs.append(str(it["message"])[:2000])

    llm_u: list[dict[str, Any]] = []
    new_t = phase4_llm.revise_scene_narration_llm(
        purpose=sc.purpose,
        narration_text=sc.narration_text,
        recommendations=recs or ["Tighten clarity for spoken documentary VO."],
        settings=settings,
        narration_style=effective_narration_style(
            project.narration_style, settings, db=db, tenant_id=project.tenant_id
        ),
        usage_sink=llm_u,
    )
    _flush_llm_usage(db, project.tenant_id, project.id, sc.id, None, llm_u)
    mode = "llm"
    if not new_t:
        note = (recs[0] if recs else "Critic follow-up")[:1200]
        base = (sc.narration_text or "").strip()
        new_t = f"{base}\n\n[Editor revision: {note}]".strip() if base else note
        mode = "fallback"

    sc.narration_text = sanitize_jsonb_text(new_t, 12_000)
    if sc.critic_passed is False:
        sc.critic_passed = None

    return mode, report.id


def _phase4_scene_critic_revision(db, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    sid = uuid.UUID(str(payload["scene_id"]))
    sc = db.get(Scene, sid)
    if not sc:
        raise ValueError("scene not found")
    ch = db.get(Chapter, sc.chapter_id)
    if not ch:
        raise ValueError("chapter not found")
    project = db.get(Project, ch.project_id)
    if not project:
        raise ValueError("project not found")

    mode, rid = _scene_critic_revision_apply_from_latest_report(db, sc, project, settings)
    if mode == "skip":
        raise ValueError("no critic report for scene; run scene critique first")

    return {"scene_id": str(sid), "revision_mode": mode, "prior_critic_report_id": str(rid) if rid else ""}


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


def _narration_generate(db, job: Job, settings: Any) -> dict[str, Any]:
    from director_api.providers.speech_chatterbox import synthesize_chapter_narration_mp3_chatterbox
    from director_api.providers.speech_elevenlabs import synthesize_chapter_narration_mp3_elevenlabs
    from director_api.providers.speech_gemini_tts import synthesize_chapter_narration_mp3_gemini
    from director_api.providers.speech_kokoro import synthesize_chapter_narration_mp3_kokoro
    from director_api.providers.speech_openai import synthesize_chapter_narration_mp3
    from director_api.providers.speech_placeholder import synthesize_placeholder_narration_mp3
    from director_api.providers.speech_route import resolve_chatterbox_ref_to_path, resolve_speech_narration_route

    payload = job.payload or {}
    cid = uuid.UUID(str(payload["chapter_id"]))
    tenant = str(payload.get("tenant_id") or settings.default_tenant_id)
    ch = db.get(Chapter, cid)
    if not ch:
        raise ValueError("chapter not found")
    project = db.get(Project, ch.project_id)
    if not project or project.tenant_id != tenant:
        raise ValueError("project not found")

    ar_uuid = _payload_agent_run_uuid(payload)
    if ar_uuid is not None and _agent_run_checkpoint(db, ar_uuid) == "stop":
        return {"ok": False, "error_message": "Stopped by user", "stopped": True}

    scenes = list(
        db.scalars(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.order_index)).all()
    )
    body = phase3_svc.resolve_chapter_narration_tts_body(ch, scenes)
    if not body:
        raise ValueError(
            "no substantive narration to synthesize — add chapter script_text or scene narration; "
            "outline producer notes are not spoken."
        )
    body = sanitize_jsonb_text(body, 80_000)

    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        raise ValueError("ffmpeg not found on PATH (required to merge TTS segments)")

    provider, opts = resolve_speech_narration_route(project.preferred_speech_provider, settings)
    ffprobe_bin = (settings.ffprobe_bin or "ffprobe").strip() or "ffprobe"
    timeout_sec = float(settings.ffmpeg_timeout_sec)

    storage = FilesystemStorage(settings.local_storage_root)
    storage_root = Path(settings.local_storage_root).resolve()
    vtt_key = f"narrations/{project.id}/{ch.id}.vtt"
    vtt_disk = storage.get_path(vtt_key)
    try:
        if path_is_readable_file(vtt_disk):
            vtt_disk.unlink()
    except OSError:
        pass

    webvtt: str | None = None
    if provider == "placeholder":
        mp3_bytes, dur = synthesize_placeholder_narration_mp3(
            body,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=min(timeout_sec, 600.0),
        )
        voice_cfg = {"provider": "placeholder", "kind": "ffmpeg_ding"}
        usage_provider = "placeholder"
        usage_service = "narration_tts_placeholder"
        usage_meta = {"chapter_id": str(ch.id)}
    elif provider == "kokoro":
        mp3_bytes, dur, webvtt = synthesize_chapter_narration_mp3_kokoro(
            body,
            settings,
            voice=str(opts.get("voice") or "af_bella"),
            lang_code=str(opts.get("lang_code") or "a"),
            speed=float(opts.get("speed") or 1.0),
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        voice_cfg = {
            "provider": "kokoro",
            "voice": str(opts.get("voice") or "af_bella"),
            "lang_code": str(opts.get("lang_code") or "a"),
            "speed": float(opts.get("speed") or 1.0),
            "repo_id": (getattr(settings, "kokoro_hf_repo_id", None) or "hexgrad/Kokoro-82M").strip(),
        }
        usage_provider = "kokoro"
        usage_service = "narration_tts_kokoro"
        usage_meta = {"chapter_id": str(ch.id), **voice_cfg}
    elif provider == "chatterbox_turbo":
        ref_p = resolve_chatterbox_ref_to_path(str(opts.get("ref_path") or ""), storage_root=storage_root)
        mp3_bytes, dur = synthesize_chapter_narration_mp3_chatterbox(
            body,
            settings,
            variant="turbo",
            ref_audio_path=ref_p,
            language_id=None,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        voice_cfg = {
            "provider": "chatterbox_turbo",
            "ref_path": str(ref_p),
        }
        usage_provider = "chatterbox"
        usage_service = "narration_tts_chatterbox_turbo"
        usage_meta = {"chapter_id": str(ch.id), "ref_path": str(ref_p)}
    elif provider == "chatterbox_mtl":
        ref_p = resolve_chatterbox_ref_to_path(str(opts.get("ref_path") or ""), storage_root=storage_root)
        mp3_bytes, dur = synthesize_chapter_narration_mp3_chatterbox(
            body,
            settings,
            variant="mtl",
            ref_audio_path=ref_p,
            language_id=str(opts.get("language_id") or "en"),
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        voice_cfg = {
            "provider": "chatterbox_mtl",
            "ref_path": str(ref_p),
            "language_id": str(opts.get("language_id") or "en"),
        }
        usage_provider = "chatterbox"
        usage_service = "narration_tts_chatterbox_mtl"
        usage_meta = {"chapter_id": str(ch.id), **voice_cfg}
    elif provider == "elevenlabs":
        mp3_bytes, dur = synthesize_chapter_narration_mp3_elevenlabs(
            body,
            settings,
            voice_id=str(opts.get("voice_id") or ""),
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        model = (getattr(settings, "elevenlabs_model_id", None) or "eleven_multilingual_v2").strip()
        voice_cfg = {
            "provider": "elevenlabs",
            "model": model,
            "voice_id": str(opts.get("voice_id") or getattr(settings, "elevenlabs_voice_id", "") or ""),
        }
        usage_provider = "elevenlabs"
        usage_service = "narration_tts_elevenlabs"
        usage_meta = {"chapter_id": str(ch.id), "voice_id": voice_cfg["voice_id"], "model": model}
    elif provider == "gemini":
        voice_g = str(opts.get("voice") or "Kore")
        mp3_bytes, dur = synthesize_chapter_narration_mp3_gemini(
            body,
            settings,
            voice_name=voice_g,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        model = (getattr(settings, "gemini_tts_model", None) or "gemini-2.5-flash-preview-tts").strip()
        voice_cfg = {"provider": "gemini", "model": model, "voice": voice_g}
        usage_provider = "gemini"
        usage_service = "narration_tts_gemini"
        usage_meta = {"chapter_id": str(ch.id), "voice": voice_g, "model": model}
    else:
        voice = str(opts.get("voice") or "alloy")
        mp3_bytes, dur = synthesize_chapter_narration_mp3(
            body,
            settings,
            voice=voice,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=timeout_sec,
        )
        model = (settings.openai_tts_model or "tts-1").strip() or "tts-1"
        voice_cfg = {"provider": "openai", "model": model, "voice": voice}
        usage_provider = "openai"
        usage_service = "narration_tts_openai"
        usage_meta = {"chapter_id": str(ch.id), "voice": voice, "model": model}

    if provider == "kokoro" and webvtt:
        storage.put_bytes(vtt_key, webvtt.encode("utf-8"), content_type="text/vtt")

    key = f"narrations/{project.id}/{ch.id}.mp3"
    audio_url = storage.put_bytes(key, mp3_bytes, content_type="audio/mpeg")

    for nt in db.scalars(
        select(NarrationTrack).where(NarrationTrack.chapter_id == ch.id, NarrationTrack.scene_id.is_(None))
    ).all():
        db.delete(nt)

    nt = NarrationTrack(
        id=uuid.uuid4(),
        tenant_id=tenant,
        project_id=project.id,
        chapter_id=ch.id,
        scene_id=None,
        text=body,
        voice_config_json=voice_cfg,
        audio_url=audio_url,
        duration_sec=dur,
    )
    db.add(nt)
    _record_usage(
        db,
        tenant_id=tenant,
        project_id=project.id,
        scene_id=None,
        asset_id=None,
        provider=usage_provider,
        service_type=usage_service,
        meta=usage_meta,
    )
    return {"narration_track_id": str(nt.id), "duration_sec": dur}


def _narration_generate_scene(db, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    ar_uuid = _payload_agent_run_uuid(payload)
    if ar_uuid is not None and _agent_run_checkpoint(db, ar_uuid) == "stop":
        return {"ok": False, "error_message": "Stopped by user", "stopped": True}
    from director_api.services.scene_narration_tts import run_scene_narration_tts_job

    return run_scene_narration_tts_job(db, job, settings)


def _subtitles_generate(db, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    pid = uuid.UUID(str(payload["project_id"]))
    tenant = str(payload.get("tenant_id") or settings.default_tenant_id)
    project = db.get(Project, pid)
    if not project or project.tenant_id != tenant:
        raise ValueError("project not found")
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.order_index)).all()
    )
    scenes_ordered = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == pid)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    full, total_sec = assemble_project_subtitle_markdown(chapters, scenes_ordered)
    if len(full) < 8:
        raise ValueError("no script text for subtitles — add scene narration scripts or chapter scripts")
    if total_sec < 5.0:
        total_sec = max(30.0, float(project.target_runtime_minutes or 15) * 60.0 * 0.05)
    vtt = script_to_webvtt(full, total_sec=total_sec)
    storage_root = Path(settings.local_storage_root).resolve()
    out = storage_root / "exports" / str(pid) / "subtitles.vtt"
    mkdir_parent(out)
    out.write_text(vtt, encoding="utf-8")
    return {"subtitle_url": f"file://{out.resolve()}", "bytes": path_stat(out).st_size, "total_sec": total_sec}


def _attach_latest_music_bed_if_missing(
    db: Any,
    tv: TimelineVersion,
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    storage_root: Path,
    director_auth_enabled: bool = True,
) -> None:
    """If the timeline has no ``music_bed_id``, attach the newest *usable* bed.

    Scope matches ``GET /v1/projects/{id}/music-beds``: project-local beds plus, when auth is on,
    the latest agent run's user's library uploads; when auth is off, any bed in the tenant.

    Usable = non-empty ``license_or_source_ref`` and ``storage_url`` resolving to a readable file
    under ``storage_root``. Skips beds with missing files so final mux always has audio on disk.
    """
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    if tj.get("music_bed_id"):
        return
    stmt = select(MusicBed).where(MusicBed.tenant_id == tenant_id)
    if director_auth_enabled:
        uid = db.scalar(
            select(AgentRun.started_by_user_id)
            .where(AgentRun.project_id == project_id, AgentRun.tenant_id == tenant_id)
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        if uid is not None:
            stmt = stmt.where(
                or_(MusicBed.project_id == project_id, MusicBed.uploaded_by_user_id == uid)
            )
        else:
            stmt = stmt.where(MusicBed.project_id == project_id)
    stmt = stmt.order_by(MusicBed.created_at.desc())
    rows = list(db.scalars(stmt).all())
    chosen: MusicBed | None = None
    for mb_row in rows:
        if not (mb_row.license_or_source_ref or "").strip():
            continue
        su = (mb_row.storage_url or "").strip()
        if not su:
            continue
        mp = path_from_storage_url(su, storage_root=storage_root)
        if mp is not None and path_is_readable_file(mp):
            chosen = mb_row
            break
    if chosen is None:
        return
    n: dict[str, Any] = {**tj, "music_bed_id": str(chosen.id)}
    if "mix_music_volume" not in n:
        n["mix_music_volume"] = 0.28
    validate_timeline_document(n)
    tv.timeline_json = n
    flag_modified(tv, "timeline_json")
    db.commit()
    log.info(
        "timeline_music_bed_auto_attached",
        timeline_version_id=str(tv.id),
        music_bed_id=str(chosen.id),
    )


def _final_cut(db, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    tv_id = uuid.UUID(str(payload["timeline_version_id"]))
    tenant = str(payload.get("tenant_id") or settings.default_tenant_id)
    project_id = uuid.UUID(str(payload["project_id"]))
    tv = db.get(TimelineVersion, tv_id)
    if not tv or tv.tenant_id != tenant or tv.project_id != project_id:
        raise ValueError("timeline version not found")
    project = db.get(Project, project_id)
    if not project or project.tenant_id != tenant:
        raise ValueError("project not found")

    storage_root = Path(settings.local_storage_root).resolve()
    allow_unapproved = bool((payload or {}).get("allow_unapproved_media"))
    _phase5_auto_heal_before_export(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved,
    )
    fine_p = storage_root / "exports" / str(project_id) / str(tv_id) / "fine_cut.mp4"
    rough_p = storage_root / "exports" / str(project_id) / str(tv_id) / "rough_cut.mp4"
    if not path_is_readable_file(fine_p) and not path_is_readable_file(rough_p):
        log.info("final_cut_prerun_rough_cut", timeline_version_id=str(tv_id), project_id=str(project_id))
        _rough_cut(db, job, settings)
        # Commit the rough_cut DB state (tv.render_status, tv.output_url) before refreshing so
        # that a subsequent final_cut failure doesn't leave the DB in an inconsistent state
        # (file on disk, but DB still showing the pre-rough-cut render_status).
        db.commit()
        db.refresh(tv)
        rough_p = storage_root / "exports" / str(project_id) / str(tv_id) / "rough_cut.mp4"
        fine_p = storage_root / "exports" / str(project_id) / str(tv_id) / "fine_cut.mp4"

    db.refresh(tv)
    _attach_latest_music_bed_if_missing(
        db,
        tv,
        tenant_id=tenant,
        project_id=project.id,
        storage_root=storage_root,
        director_auth_enabled=bool(getattr(settings, "director_auth_enabled", True)),
    )
    db.refresh(tv)

    readiness = compute_phase5_readiness(
        db,
        project_id=project.id,
        tenant_id=tenant,
        timeline_version_id=tv_id,
        storage_root=storage_root,
        export_stage="final_cut",
        allow_unapproved_media=allow_unapproved,
    )
    if not readiness.get("ready"):
        raise ValueError(format_phase5_readiness_failure(readiness))

    base_video = fine_p if path_is_readable_file(fine_p) else rough_p
    if not path_is_readable_file(base_video):
        raise ValueError("run rough_cut first (missing rough_cut.mp4)")

    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (settings.ffprobe_bin or "ffprobe").strip() or "ffprobe"
    if not shutil.which(ffmpeg_bin):
        raise ValueError("ffmpeg not found for final_cut")

    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    mb_ref = tj.get("music_bed_id")
    music_path: Path | None = None
    mb: MusicBed | None = None
    if mb_ref:
        try:
            mb = db.get(MusicBed, uuid.UUID(str(mb_ref)))
        except (ValueError, TypeError):
            mb = None
        if mb and mb.storage_url:
            mp = path_from_storage_url(mb.storage_url, storage_root=storage_root)
            if mp and path_is_readable_file(mp):
                music_path = mp
        if music_path is None:
            log.warning(
                "final_cut_music_bed_unresolved",
                timeline_version_id=str(tv_id),
                music_bed_id=str(mb_ref),
                has_row=mb is not None,
            )

    try:
        mix_mv = float(tj.get("mix_music_volume", 0.28) or 0.28)
    except (TypeError, ValueError):
        mix_mv = 0.28
    try:
        mix_nv = float(tj.get("mix_narration_volume", 1.0) or 1.0)
    except (TypeError, ValueError):
        mix_nv = 1.0
    mix_mv = max(0.0, min(mix_mv, 1.0))
    mix_nv = max(0.0, min(mix_nv, 4.0))

    out_final = storage_root / "exports" / str(project_id) / str(tv_id) / "final_cut.mp4"
    mkdir_parent(out_final)

    card_sec = _export_chapter_title_card_sec(settings)
    manifest_fc = _build_timeline_export_manifest(
        db, project, tv, settings, allow_unapproved_media=allow_unapproved
    )

    slots_orig = _final_cut_audio_slots_from_manifest(
        db,
        manifest_fc,
        card_sec=card_sec,
        storage_root=storage_root,
        ffprobe_bin=ffprobe_bin,
        timeout_sec=float(settings.ffmpeg_timeout_sec),
    )
    manifest_exp, slots_fc = _expand_manifest_and_slots_for_full_narration(
        db,
        manifest_fc,
        card_sec=card_sec,
        project_id=project_id,
        storage_root=storage_root,
        ffprobe_bin=ffprobe_bin,
        timeout_sec=float(settings.ffmpeg_timeout_sec),
        tail_padding_sec=_scene_vo_tail_padding_sec(settings),
    )
    sum_o = _slots_total_duration(slots_orig)
    sum_e = _slots_total_duration(slots_fc)
    vid_len = (
        float(
            ffprobe_duration_seconds(
                base_video,
                ffprobe_bin=ffprobe_bin,
                timeout_sec=float(settings.ffmpeg_timeout_sec),
            )
        )
        if path_is_readable_file(base_video)
        else 0.0
    )
    want_visual_recompile = (sum_e > sum_o + 0.05) or (
        path_is_readable_file(base_video) and abs(sum_e - vid_len) > 0.25
    )
    need_visual_recompile = False
    if (
        want_visual_recompile
        and manifest_fc
        and settings.ffmpeg_compile_enabled
        and shutil.which(ffmpeg_bin)
    ):
        need_visual_recompile = True
        log.info(
            "final_cut_rebuild_visuals_for_full_narration",
            sum_orig_sec=sum_o,
            sum_expanded_sec=sum_e,
            base_video_sec=vid_len,
        )
        _rough_cut(db, job, settings, manifest_override=manifest_exp)
        if path_is_readable_file(fine_p):
            try:
                fine_p.unlink()
            except OSError:
                pass
        overlays_raw = tj.get("overlays")
        overlays_list = overlays_raw if isinstance(overlays_raw, list) else []
        if any(isinstance(x, dict) for x in overlays_list):
            try:
                _fine_cut(db, job, settings)
            except ValueError as e:
                log.warning("final_cut_fine_cut_after_narration_expand_failed", error=str(e)[:400])
        db.commit()
        db.refresh(tv)
        base_video = fine_p if path_is_readable_file(fine_p) else rough_p

    if not path_is_readable_file(base_video):
        raise ValueError("run rough_cut first (missing rough_cut.mp4)")

    narr_path: Path | None = None
    narr_concat_tmp: Path | None = None
    scene_slot_count = 0
    try:
        scene_slot_count = len(slots_fc)
        narr_merged, _stem_cleanup = _build_scene_timeline_narration_stem(
            db,
            project_id,
            slots_fc,
            out_final.parent,
            ffmpeg_bin=ffmpeg_bin,
            timeout_sec=float(settings.ffmpeg_timeout_sec),
            storage_root=storage_root,
        )
        if narr_merged is None:
            log.warning(
                "final_cut_narration_stem_empty_all_slots_zero_duration",
                timeline_version_id=str(tv_id),
                slot_count=scene_slot_count,
            )
        narr_path = narr_merged
        narr_concat_tmp = narr_merged

        mux_meta = mux_video_with_narration_and_music(
            base_video,
            out_final,
            narration_audio_path=narr_path,
            music_audio_path=music_path,
            music_volume=mix_mv,
            narration_volume=mix_nv,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=float(settings.ffmpeg_timeout_sec),
        )
        mux_meta = {
            **mux_meta,
            "final_cut_narration_mode": "scene_timeline",
            "mix_music_volume": mix_mv,
            "mix_narration_volume": mix_nv,
            "narration_timeline_slots": scene_slot_count,
            "narration_visual_sum_orig_sec": sum_o,
            "narration_visual_sum_expanded_sec": sum_e,
            "narration_visual_recompiled": bool(need_visual_recompile),
        }
        if card_sec > 0 and narr_path is not None:
            mux_meta = {**mux_meta, "export_chapter_title_card_sec": card_sec}
    finally:
        # Clean up the merged narration stem written by _build_scene_timeline_narration_stem.
        # Per-slot segment files are already deleted inside that helper's own finally block.
        if narr_concat_tmp is not None and path_is_readable_file(narr_concat_tmp):
            try:
                narr_concat_tmp.unlink()
            except OSError:
                pass

    burn_key = (payload or {}).get("burn_subtitles_into_video", None)
    if burn_key is None:
        burn_subs = bool(getattr(settings, "burn_subtitles_in_final_cut_default", False))
    else:
        burn_subs = bool(burn_key)
    sub_path = storage_root / "exports" / str(project_id) / "subtitles.vtt"
    if burn_subs and path_is_readable_file(sub_path):
        from director_api.services.video_subtitle_burn import burn_webvtt_onto_mp4

        burn_webvtt_onto_mp4(
            video_in=out_final,
            vtt_path=sub_path,
            video_out=out_final,
            ffmpeg_bin=ffmpeg_bin,
            timeout_sec=float(settings.ffmpeg_timeout_sec),
        )
        mux_meta = {**mux_meta, "subtitles_burned": True, "subtitles_source": str(sub_path.resolve())}

    tv.render_status = "final_compiled"
    tv.output_url = f"file://{out_final.resolve()}"
    from director_api.services.youtube_pipeline import try_youtube_auto_upload

    try_youtube_auto_upload(
        db,
        settings,
        tenant_id=tenant,
        project_id=project_id,
        project_title=(project.title or "Export"),
        timeline_version_id=tv_id,
    )
    return {
        "timeline_version_id": str(tv.id),
        "output_url": tv.output_url,
        "mux": mux_meta,
    }


def _phase5_auto_heal_before_export(
    db: Any,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> dict[str, int]:
    """
    Reconcile timeline clips to viable scene media and auto-approve succeeded assets on disk when
    export preflight requires approval — persists DB + ``tv.timeline_json`` before readiness checks.
    """
    from director_api.services import timeline_image_repair as timeline_image_repair_svc

    stats = timeline_image_repair_svc.auto_heal_project_timeline_for_export(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )
    if (
        stats.get("relinked_assets")
        or stats.get("rebound_clips")
        or stats.get("storyboard_synced_clips")
        or stats.get("approved_scene_stills")
        or stats.get("approved_timeline_assets")
        or stats.get("reconciled_clips")
    ):
        if stats.get("reconciled_clips") or stats.get("rebound_clips") or stats.get("storyboard_synced_clips"):
            flag_modified(tv, "timeline_json")
        db.commit()
        db.refresh(tv)
        log.info(
            "phase5_export_auto_heal",
            project_id=str(project.id),
            timeline_version_id=str(tv.id),
            **stats,
        )
    return stats


def _export_bundle(db, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    tv_id = uuid.UUID(str(payload["timeline_version_id"]))
    project_id = uuid.UUID(str(payload["project_id"]))
    tenant = str(payload.get("tenant_id") or settings.default_tenant_id)
    tv = db.get(TimelineVersion, tv_id)
    if not tv or tv.tenant_id != tenant or tv.project_id != project_id:
        raise ValueError("timeline version not found")
    include_sub = bool(payload.get("include_subtitles", True))
    storage_root = Path(settings.local_storage_root).resolve()
    base = storage_root / "exports" / str(project_id) / str(tv_id)
    final_p = base / "final_cut.mp4"
    if not path_is_readable_file(final_p):
        fine_p = base / "fine_cut.mp4"
        final_p = fine_p if path_is_readable_file(fine_p) else base / "rough_cut.mp4"
    if not path_is_readable_file(final_p):
        raise ValueError("no compiled video; run rough_cut or final_cut first")
    sub = storage_root / "exports" / str(project_id) / "subtitles.vtt"
    bundle: dict[str, Any] = {
        "video_path": str(final_p.resolve()),
        "video_url": f"file://{final_p.resolve()}",
        "subtitle_path": str(sub.resolve()) if include_sub and path_is_readable_file(sub) else None,
        "timeline_version_id": str(tv_id),
        "project_id": str(project_id),
    }
    manifest_path = base / "export_bundle.json"
    mkdir_parent(manifest_path)
    manifest_path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    bundle["bundle_manifest_path"] = str(manifest_path.resolve())
    bundle["bundle_manifest_url"] = f"file://{manifest_path.resolve()}"
    return {"bundle": bundle}


def _rough_cut(
    db,
    job: Job,
    settings: Any,
    *,
    manifest_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = job.payload or {}
    tv_id = uuid.UUID(str(payload["timeline_version_id"]))
    tenant = str(payload.get("tenant_id") or settings.default_tenant_id)
    project_id = uuid.UUID(str(payload["project_id"]))
    tv = db.get(TimelineVersion, tv_id)
    if not tv or tv.tenant_id != tenant or tv.project_id != project_id:
        raise ValueError("timeline version not found")
    project = db.get(Project, project_id)
    if not project or project.tenant_id != tenant:
        raise ValueError("project not found")
    ew, eh = _project_export_dimensions(project)

    storage_root = Path(settings.local_storage_root).resolve()
    allow_unapproved = bool((payload or {}).get("allow_unapproved_media"))
    _phase5_auto_heal_before_export(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved,
    )
    readiness = compute_phase5_readiness(
        db,
        project_id=project.id,
        tenant_id=tenant,
        timeline_version_id=tv_id,
        storage_root=storage_root,
        export_stage="rough_cut",
        allow_unapproved_media=allow_unapproved,
    )
    if not readiness.get("ready"):
        raise ValueError(format_phase5_readiness_failure(readiness))

    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    clip_xf = _timeline_clip_crossfade_sec(tj)
    if manifest_override is not None:
        manifest = manifest_override
    else:
        manifest = _build_timeline_export_manifest(
            db, project, tv, settings, allow_unapproved_media=allow_unapproved
        )

    mb_ref = tj.get("music_bed_id") if isinstance(tj, dict) else None
    mb: MusicBed | None = None
    if mb_ref:
        # Resolve the music bed so it can be included in the export manifest metadata.
        # Do NOT enforce license here — rough_cut is video-only and never uses audio;
        # the license gate belongs exclusively to final_cut / the readiness check.
        try:
            mb = db.get(MusicBed, uuid.UUID(str(mb_ref)))
        except (ValueError, TypeError):
            mb = None
        if mb is not None and mb.tenant_id != project.tenant_id:
            mb = None  # wrong tenant / orphaned reference — log and continue rather than hard-fail

    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (getattr(settings, "ffprobe_bin", None) or "ffprobe").strip() or "ffprobe"
    export_manifest: dict[str, Any] | None = None
    compile_meta: dict[str, Any] = {"invoked": False}
    output_url: str | None = None
    render_status = "manifest_ready"

    if manifest and not settings.ffmpeg_compile_enabled:
        log.warning(
            "rough_cut_manifest_only_ffmpeg_compile_disabled",
            timeline_version_id=str(tv_id),
            project_id=str(project_id),
            hint="Set ffmpeg_compile_enabled to compile an MP4; otherwise only manifest metadata is produced.",
        )

    if (
        manifest
        and settings.ffmpeg_compile_enabled
        and shutil.which(ffmpeg_bin)
    ):
        types = {str(m["asset_type"]).lower() for m in manifest}
        if types - {"image", "video"}:
            raise ValueError("ROUGH_CUT_FFMPEG: only image or video assets are supported for compile")
        try:
            out_path = storage_root / "exports" / str(project.id) / str(tv.id) / "rough_cut.mp4"
            card_sec = _export_chapter_title_card_sec(settings)
            if card_sec > 0:
                mixed_segments = _rough_cut_visual_segments_with_chapter_cards(
                    db,
                    manifest,
                    card_sec=card_sec,
                    storage_root=storage_root,
                    ffprobe_bin=ffprobe_bin,
                )
                compile_meta = compile_mixed_visual_timeline(
                    mixed_segments,
                    out_path,
                    width=ew,
                    height=eh,
                    ffmpeg_bin=ffmpeg_bin,
                    ffprobe_bin=ffprobe_bin,
                    timeout_sec=float(settings.ffmpeg_timeout_sec),
                    image_batch_crossfade_sec=clip_xf,
                )
                compile_meta["export_chapter_title_card_sec"] = card_sec
            elif len(types) > 1:
                mixed_segments = []
                for m in manifest:
                    lp = path_from_storage_url(m.get("storage_url"), storage_root=storage_root)
                    if lp is None or not path_is_readable_file(lp):
                        raise ValueError(f"missing local file for asset {m.get('asset_id')}")
                    at = str(m["asset_type"]).lower()
                    if at == "video":
                        mixed_segments.append(_rough_cut_video_segment_tuple(m, lp, ffprobe_bin=ffprobe_bin))
                    elif at == "image":
                        ds = m.get("duration_sec")
                        if ds is None or float(ds) <= 0:
                            raise ValueError(f"invalid duration_sec for image asset {m.get('asset_id')}")
                        mixed_segments.append(("image", lp, float(ds)))
                    else:
                        raise ValueError("ROUGH_CUT_FFMPEG: unsupported asset_type for compile")
                compile_meta = compile_mixed_visual_timeline(
                    mixed_segments,
                    out_path,
                    width=ew,
                    height=eh,
                    ffmpeg_bin=ffmpeg_bin,
                    ffprobe_bin=ffprobe_bin,
                    timeout_sec=float(settings.ffmpeg_timeout_sec),
                    image_batch_crossfade_sec=clip_xf,
                )
            elif types == {"video"}:
                video_segments: list[Any] = []
                for m in manifest:
                    lp = path_from_storage_url(m.get("storage_url"), storage_root=storage_root)
                    if lp is None or not path_is_readable_file(lp):
                        raise ValueError(f"missing local video file for asset {m.get('asset_id')}")
                    video_segments.append(_rough_cut_video_segment_tuple(m, lp, ffprobe_bin=ffprobe_bin))
                compile_meta = compile_mixed_visual_timeline(
                    video_segments,
                    out_path,
                    width=ew,
                    height=eh,
                    ffmpeg_bin=ffmpeg_bin,
                    ffprobe_bin=ffprobe_bin,
                    timeout_sec=float(settings.ffmpeg_timeout_sec),
                    image_batch_crossfade_sec=0.0,
                )
            elif types == {"image"}:
                slides: list[tuple[Path, float]] = []
                for m in manifest:
                    lp = path_from_storage_url(m.get("storage_url"), storage_root=storage_root)
                    if lp is None or not path_is_readable_file(lp):
                        raise ValueError(f"missing local image file for asset {m.get('asset_id')}")
                    ds = m.get("duration_sec")
                    if ds is None or float(ds) <= 0:
                        raise ValueError(f"invalid duration_sec for image asset {m.get('asset_id')}")
                    slides.append((lp, float(ds)))
                compile_meta = compile_image_slideshow(
                    slides,
                    out_path,
                    width=ew,
                    height=eh,
                    ffmpeg_bin=ffmpeg_bin,
                    timeout_sec=float(settings.ffmpeg_timeout_sec),
                    motion="none",
                    crossfade_sec=clip_xf,
                    slow_zoom=False,
                )
            else:
                raise ValueError("ROUGH_CUT_FFMPEG: unsupported asset_type for compile")
            compile_meta["invoked"] = True
            output_url = f"file://{out_path.resolve()}"
            render_status = "compiled"
            export_manifest = build_export_manifest(
                output_url=output_url,
                inputs=[
                    {
                        "role": "videotimeline",
                        "asset_id": m.get("asset_id"),
                        "url": m.get("storage_url"),
                    }
                    for m in manifest
                ]
                + (
                    [
                        {
                            "role": "music",
                            "url": mb.storage_url,
                            "license_or_source_ref": mb.license_or_source_ref,
                        }
                    ]
                    if mb and mb.storage_url
                    else []
                ),
                compile_meta={**compile_meta, "crf": 23, "preset": "veryfast"},
                ffmpeg_bin=ffmpeg_bin,
            )
        except FFmpegCompileError as e:
            raise ValueError(f"FFMPEG_FAILED: {e}") from e
    elif manifest and settings.ffmpeg_compile_enabled:
        compile_meta = {"invoked": False, "reason": "ffmpeg_binary_not_found", "ffmpeg_bin": ffmpeg_bin}

    tv.render_status = render_status
    tv.output_url = output_url
    return {
        "timeline_version_id": str(tv.id),
        "clip_count": len(manifest),
        "manifest": manifest,
        "ffmpeg": compile_meta,
        "export_manifest": export_manifest,
    }


def _fine_cut(db, job: Job, settings: Any) -> dict[str, Any]:
    """Burn timeline ``overlays`` onto ``rough_cut.mp4`` → ``fine_cut.mp4`` (local FFmpeg)."""
    payload = job.payload or {}
    tv_id = uuid.UUID(str(payload["timeline_version_id"]))
    tenant = str(payload.get("tenant_id") or settings.default_tenant_id)
    project_id = uuid.UUID(str(payload["project_id"]))
    tv = db.get(TimelineVersion, tv_id)
    if not tv or tv.tenant_id != tenant or tv.project_id != project_id:
        raise ValueError("timeline version not found")
    project = db.get(Project, project_id)
    if not project or project.tenant_id != tenant:
        raise ValueError("project not found")

    storage_root = Path(settings.local_storage_root).resolve()
    allow_unapproved = bool((payload or {}).get("allow_unapproved_media"))
    _phase5_auto_heal_before_export(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved,
    )
    base = storage_root / "exports" / str(project_id) / str(tv_id)
    rough = base / "rough_cut.mp4"
    if not path_is_readable_file(rough):
        log.info("fine_cut_prerun_rough_cut", timeline_version_id=str(tv_id), project_id=str(project_id))
        _rough_cut(db, job, settings)
        db.refresh(tv)
        rough = base / "rough_cut.mp4"

    readiness = compute_phase5_readiness(
        db,
        project_id=project.id,
        tenant_id=tenant,
        timeline_version_id=tv_id,
        storage_root=storage_root,
        export_stage="fine_cut",
        allow_unapproved_media=allow_unapproved,
    )
    if not readiness.get("ready"):
        raise ValueError(format_phase5_readiness_failure(readiness))

    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    validate_timeline_document(tj)

    if not path_is_readable_file(rough):
        raise ValueError("run rough_cut first (missing rough_cut.mp4)")

    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        raise ValueError("ffmpeg not found for fine_cut")

    overlays = tj.get("overlays")
    if not isinstance(overlays, list):
        overlays = []

    fine = base / "fine_cut.mp4"
    try:
        meta = burn_overlays_on_video(
            rough,
            fine,
            overlays,
            ffmpeg_bin=ffmpeg_bin,
            timeout_sec=float(settings.ffmpeg_timeout_sec),
        )
    except FFmpegCompileError as e:
        raise ValueError(f"FFMPEG_FINE_CUT_FAILED: {e}") from e

    return {
        "timeline_version_id": str(tv.id),
        "fine_cut_url": f"file://{fine.resolve()}",
        "ffmpeg": meta,
        "overlay_defs": len(overlays),
    }


@celery_app.task(bind=True, name="director.run_phase4_job", soft_time_limit=600, time_limit=720)
def run_phase4_job(self, job_id: str) -> None:
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


@celery_app.task(
    bind=True,
    name="director.run_phase5_job",
    soft_time_limit=_CELERY_PHASE5_SOFT_SEC,
    time_limit=_CELERY_PHASE5_HARD_SEC,
)
def run_phase5_job(self, job_id: str) -> None:
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
