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
    asset_by_id, scene_by_id, ch_by_id = manifest_prefetch_asset_hierarchy(db, manifest)
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
    asset_by_id, scene_by_id, ch_by_id = manifest_prefetch_asset_hierarchy(db, manifest)
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
            narr = get_export_narration_budget_sec_for_scene(
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


def _latest_chapter_narration_audio_path(
    db: Any,
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
    storage_root: Path,
) -> Path | None:
    """Local path for chapter-level TTS (``scene_id`` is NULL), or None."""
    nt = db.scalar(
        select(NarrationTrack)
        .where(
            NarrationTrack.project_id == project_id,
            NarrationTrack.chapter_id == chapter_id,
            NarrationTrack.scene_id.is_(None),
            NarrationTrack.audio_url.isnot(None),
        )
        .order_by(NarrationTrack.created_at.desc())
    )
    if not nt:
        return None
    np = path_from_storage_url((nt.audio_url or "").strip(), storage_root=storage_root)
    if np is not None and path_is_readable_file(np):
        return np
    return None


def _build_scene_timeline_narration_stem(
    db: Any,
    project_id: uuid.UUID,
    slots: list[tuple[float, uuid.UUID | None]],
    out_dir: Path,
    *,
    ffmpeg_bin: str,
    timeout_sec: float,
    storage_root: Path,
    ffprobe_bin: str = "ffprobe",
) -> tuple[Path | None, list[Path]]:
    """Concat silence + per-scene narration segments to one AAC track; returns (merged_path, paths_to_delete).

    Slot durations should already include **at least** spoken VO + configured tail padding for the
    first timeline clip of each scene (see ``_expand_manifest_and_slots_for_full_narration``) so
    padding/trims align with the visual edit.

    When the same ``scene_id`` appears in **multiple** consecutive timeline clips (multi-clip beats),
    narration is **sliced sequentially**: clip 1 gets seconds [0, slot_dur), clip 2 gets
    [slot_dur, 2*slot_dur), and so on, so the full VO plays across the concatenated visuals.

    If there is no per-scene VO file but the chapter has **chapter-level** TTS (one
    ``NarrationTrack`` with ``scene_id`` NULL), that file is walked in timeline order so every
    chapter still speaks in the export.
    """
    parts: list[Path] = []
    cleanup: list[Path] = []
    scene_voice_offset_sec: dict[uuid.UUID, float] = {}
    chapter_stream_offset: dict[uuid.UUID, float] = {}
    chapter_path_cache: dict[uuid.UUID, Path | None] = {}

    def _chapter_audio_path(ch_id: uuid.UUID) -> Path | None:
        if ch_id not in chapter_path_cache:
            chapter_path_cache[ch_id] = _latest_chapter_narration_audio_path(
                db, project_id, ch_id, storage_root
            )
        return chapter_path_cache[ch_id]

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

        sc_row = db.get(Scene, sid)
        ch_id: uuid.UUID | None = sc_row.chapter_id if sc_row else None

        nt = db.scalar(
            select(NarrationTrack)
            .where(
                NarrationTrack.project_id == project_id,
                NarrationTrack.scene_id == sid,
                NarrationTrack.audio_url.isnot(None),
            )
            .order_by(NarrationTrack.created_at.desc())
        )
        np = path_from_storage_url((nt.audio_url or "") if nt else "", storage_root=storage_root)
        used_scene = bool(nt and np is not None and path_is_readable_file(np))

        filled = False
        if used_scene:
            off = float(scene_voice_offset_sec.get(sid, 0.0))
            seg = out_dir / f"_seg_{uuid.uuid4().hex}.m4a"
            try:
                normalize_audio_segment_to_duration(
                    np,
                    seg,
                    slot_dur,
                    start_offset_sec=off,
                    ffmpeg_bin=ffmpeg_bin,
                    ffprobe_bin=ffprobe_bin,
                    timeout_sec=min(timeout_sec, 600.0),
                )
            except FFmpegCompileError as _narr_enc_err:
                log.warning(
                    "scene_timeline_narration_encode_failed_substituting_silence",
                    scene_id=str(sid),
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
                filled = True
            else:
                scene_voice_offset_sec[sid] = off + float(slot_dur)
                parts.append(seg)
                cleanup.append(seg)
                filled = True

        if not filled and ch_id is not None:
            chp = _chapter_audio_path(ch_id)
            if chp is not None:
                off_ch = float(chapter_stream_offset.get(ch_id, 0.0))
                seg = out_dir / f"_seg_{uuid.uuid4().hex}.m4a"
                try:
                    normalize_audio_segment_to_duration(
                        chp,
                        seg,
                        slot_dur,
                        start_offset_sec=off_ch,
                        ffmpeg_bin=ffmpeg_bin,
                        ffprobe_bin=ffprobe_bin,
                        timeout_sec=min(timeout_sec, 600.0),
                    )
                except FFmpegCompileError as _narr_enc_err:
                    log.warning(
                        "chapter_timeline_narration_encode_failed_substituting_silence",
                        scene_id=str(sid),
                        chapter_id=str(ch_id),
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
                    scene_id=str(sid),
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
        elif not filled:
            log.warning(
                "scene_timeline_narration_missing_substituting_silence",
                scene_id=str(sid),
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

        if ch_id is not None and _chapter_audio_path(ch_id) is not None:
            chapter_stream_offset[ch_id] = chapter_stream_offset.get(ch_id, 0.0) + float(slot_dur)
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
    asset_by_id, scene_by_id, ch_by_id = manifest_prefetch_asset_hierarchy(db, manifest)
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



def _bind_asset_local_file(asset: Asset, url: str, storage_key: str) -> None:
    """Set storage URLs and a stable relative key so the API can resolve files if file:// parsing drifts."""
    asset.storage_url = url
    asset.preview_url = url
    pj = dict(asset.params_json) if isinstance(asset.params_json, dict) else {}
    pj["storage_key"] = storage_key
    asset.params_json = pj




# Deferred: phase impl modules may import this file indirectly — avoid import cycles.
from director_api.tasks.phase3_impl import (  # noqa: E402
    _phase3_image_generate,
    _phase3_scene_extend,
    _phase3_scenes_generate,
    _phase3_video_generate,
)
from director_api.tasks.phase4_impl import (  # noqa: E402
    _phase4_chapter_critique,
    _phase4_scene_critique,
    _phase4_scene_critic_revision,
    _phase4_scene_critique_core,
    _scene_critic_revision_apply_from_latest_report,
)
from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export  # noqa: E402


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
        require_scene_narration_tracks=bool((payload or {}).get("require_scene_narration_tracks")),
    )
    if not readiness.get("ready"):
        raise_phase5_gate(readiness)

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

    mix_mv = mix_music_volume_from_timeline(tj)
    mix_nv = mix_narration_volume_from_timeline(tj)

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
            ffprobe_bin=(getattr(settings, "ffprobe_bin", None) or "ffprobe").strip() or "ffprobe",
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


def _append_timeline_export_warnings(tv: TimelineVersion, messages: list[str]) -> None:
    """Persist user-visible export warnings on the timeline document (e.g. manifest-only rough cut)."""
    msgs = [str(m).strip() for m in messages if m and str(m).strip()]
    if not msgs:
        return
    tj: dict[str, Any] = dict(tv.timeline_json) if isinstance(tv.timeline_json, dict) else {}
    existing = tj.get("export_warnings")
    cur: list[str] = [str(x) for x in existing] if isinstance(existing, list) else []
    for w in msgs:
        if w not in cur:
            cur.append(w)
    tj["export_warnings"] = cur
    tv.timeline_json = tj
    flag_modified(tv, "timeline_json")


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
    ew, eh = frame_pixel_size(coerce_frame_aspect_ratio(getattr(project, "frame_aspect_ratio", None)))

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
        require_scene_narration_tracks=bool((payload or {}).get("require_scene_narration_tracks")),
    )
    if not readiness.get("ready"):
        raise_phase5_gate(readiness)

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

    export_warn: list[str] = []
    if manifest and not settings.ffmpeg_compile_enabled:
        export_warn.append(
            "FFmpeg compile is disabled (ffmpeg_compile_enabled=false). "
            "Only timeline manifest metadata was updated — no rough_cut.mp4 was produced. "
            "Enable compile in workspace Settings or environment to generate an MP4."
        )
    elif manifest and settings.ffmpeg_compile_enabled and not shutil.which(ffmpeg_bin):
        export_warn.append(
            f"FFmpeg binary not found ({ffmpeg_bin!r} not on PATH). "
            "Rough cut did not write rough_cut.mp4. Install ffmpeg or set FFMPEG_BIN."
        )
    if export_warn:
        _append_timeline_export_warnings(tv, export_warn)

    tv.render_status = render_status
    tv.output_url = output_url
    return {
        "timeline_version_id": str(tv.id),
        "clip_count": len(manifest),
        "manifest": manifest,
        "ffmpeg": compile_meta,
        "export_manifest": export_manifest,
        "export_warnings": export_warn,
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
        require_scene_narration_tracks=bool((payload or {}).get("require_scene_narration_tracks")),
    )
    if not readiness.get("ready"):
        raise_phase5_gate(readiness)

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
