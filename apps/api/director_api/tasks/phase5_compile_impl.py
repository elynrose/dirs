"""Phase 5 compile, narration, and export manifest helpers (canonical)."""

from __future__ import annotations

import copy
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm.attributes import flag_modified

from director_api.config import Settings
from director_api.db.models import (
    Asset,
    Chapter,
    Job,
    MusicBed,
    NarrationTrack,
    Project,
    Scene,
    TimelineVersion,
)
from director_api.logging_config import get_logger
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
from director_api.services import phase3 as phase3_svc
from director_api.services.research_service import sanitize_jsonb_text
from director_api.storage.filesystem import FilesystemStorage
from director_api.tasks.media_normalize_helpers import _project_export_dimensions
from director_api.tasks.worker_helpers import record_usage as _record_usage, worker_tenant_id
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

from director_api.tasks.agent_run_control import (
    agent_run_checkpoint as _agent_run_checkpoint,
    payload_agent_run_uuid as _payload_agent_run_uuid,
)
from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export

log = get_logger(__name__)

def _export_chapter_title_card_sec(settings: Any) -> float:
    """Workspace setting: black title-card duration before each chapter in rough/final export (0 = disabled)."""
    try:
        v = float(getattr(settings, "export_chapter_title_card_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(30.0, v))


def _export_outro_music_tail_sec(settings: Any) -> float:
    """Workspace setting: seconds of held final visual + music after the last narration (final cut; 0 = disabled)."""
    try:
        v = float(getattr(settings, "export_outro_music_tail_sec", 5.0) or 0.0)
    except (TypeError, ValueError):
        return 5.0
    return max(0.0, min(30.0, v))


DEFAULT_CLIP_CROSSFADE_SEC = 0.65


def _timeline_clip_crossfade_sec(tj: dict[str, Any] | None) -> float:
    """Timeline JSON: dissolve between consecutive stills in rough-cut image batches (0–2s)."""
    if not isinstance(tj, dict):
        return DEFAULT_CLIP_CROSSFADE_SEC
    raw = tj.get("clip_crossfade_sec")
    if raw is None:
        return DEFAULT_CLIP_CROSSFADE_SEC
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = DEFAULT_CLIP_CROSSFADE_SEC
    return max(0.0, min(v, 2.0))


def _visual_crossfade_pad_sec(settings: Any, clip_xf: float) -> float:
    """Padding added to visual clip durations when expanding for full narration.

    CPU still slideshows dissolve between slides; each overlap shortens total video length,
    so ``clip_xf`` is added back per clip (audio slots stay unpadded). GPU stills are
    pre-rendered to MP4 and concatenated with hard cuts (``image_batch_crossfade_sec=0``);
    applying the same pad would make every scene's visual longer than its audio slot and
    drift narration ahead of the picture.
    """
    from director_api.services.still_motion import resolve_still_motion_renderer

    if resolve_still_motion_renderer(settings) == "gpu":
        return 0.0
    return max(0.0, float(clip_xf))


def _drop_stale_fine_cut_if_mismatch(
    fine_p: Path,
    *,
    expected_sec: float,
    ffprobe_bin: str,
    timeout_sec: float,
) -> bool:
    """Remove ``fine_cut.mp4`` when its length disagrees with the narration timeline."""
    if not path_is_readable_file(fine_p):
        return False
    try:
        fl = float(
            ffprobe_duration_seconds(
                fine_p,
                ffprobe_bin=ffprobe_bin,
                timeout_sec=min(120.0, float(timeout_sec)),
            )
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError, TypeError):
        return False
    if fl <= 0 or abs(float(expected_sec) - fl) <= 0.25:
        return False
    try:
        fine_p.unlink()
    except OSError:
        pass
    log.info(
        "final_cut_dropped_stale_fine_cut",
        fine_cut_sec=fl,
        expected_sec=float(expected_sec),
    )
    return True


def _clear_gpu_still_motion_cache(cache_dir: Path) -> None:
    """Drop cached GPU Ken Burns MP4s so recompiles pick up new slot durations."""
    if not cache_dir.is_dir():
        return
    try:
        shutil.rmtree(cache_dir)
    except OSError as e:
        log.warning("gpu_still_motion_cache_clear_failed", error=str(e)[:200])


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
            try:
                duration_sec = float(getattr(settings, "scene_clip_duration_sec", 10) or 10)
            except (TypeError, ValueError):
                duration_sec = 10.0
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
    outro_tail_sec: float = 0.0,
    visual_crossfade_pad_sec: float = 0.0,
) -> tuple[list[dict[str, Any]], list[tuple[float, uuid.UUID | None]]]:
    """Widen the first timeline clip per scene so visuals run at least VO length + tail padding (export).

    ``outro_tail_sec`` (>0) additionally holds the **final** clip and its audio slot for that many extra
    seconds so the music bed keeps playing after the last scene's narration finishes before the video ends.

    ``visual_crossfade_pad_sec`` (>0) is added to each visual clip's on-screen duration **without** touching
    the matching audio slot. Slide-to-slide crossfades overlap adjacent clips by that many seconds, which
    otherwise shrinks the compiled video below the narration timeline (silently trimming trailing VO) and
    drifts visuals ahead of the audio. Padding each clip by the crossfade amount makes each scene's slide
    begin dissolving in exactly as its narration starts (in sync) and keeps the compiled video at least as
    long as the narration stem, so no narration is cut while the dissolves are preserved.
    """
    xf_pad = max(0.0, float(visual_crossfade_pad_sec))
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
            # Audio slot stays ``new_dur``; the visual gets the crossfade overlap added back so the
            # dissolve does not eat into the scene's on-screen (and in-sync narration) time.
            adjusted[mi]["duration_sec"] = new_dur + xf_pad
        mi += 1

    # Outro tail: extend the final visual + its audio slot so the music bed plays out after the last
    # scene's narration ends (narration is already fully contained in the scene's first clip, so the
    # extra seconds are music-only). The final slot always maps to the last manifest clip because
    # chapter-title card slots are inserted *before* a chapter's first clip, never after.
    if outro_tail_sec > 0 and slots and adjusted:
        last_dur, last_sid = slots[-1]
        slots[-1] = (float(last_dur) + float(outro_tail_sec), last_sid)
        last_at = str(adjusted[-1].get("asset_type") or "").lower()
        if last_at in ("image", "video"):
            base_dur = adjusted[-1].get("duration_sec")
            base_dur = float(base_dur) if base_dur is not None else float(last_dur)
            adjusted[-1]["duration_sec"] = base_dur + float(outro_tail_sec)

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


def _gpu_still_motion_mp4(
    m: dict[str, Any],
    lp: Path,
    dur: float,
    *,
    settings: Any,
    cache_dir: Path,
    ffmpeg_bin: str,
    width: int,
    height: int,
    timeout: float,
) -> Path:
    """Render (and cache) a GPU Ken Burns MP4 for a still. Falls back to CPU inside the renderer."""
    from director_api.services.still_motion import pick_motion_for_asset, render_still_motion_mp4

    motion, direction = pick_motion_for_asset(m["asset_id"], settings)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"{m['asset_id']}_{int(round(float(dur) * 1000))}_{motion}_{direction}.mp4"
    out = cache_dir / key
    if path_is_readable_file(out) and path_stat(out).st_size >= 32:
        return out
    tmp = out.with_suffix(".tmp.mp4")
    render_still_motion_mp4(
        lp,
        tmp,
        duration_sec=float(dur),
        width=width,
        height=height,
        settings=settings,
        ffmpeg_bin=ffmpeg_bin,
        timeout_sec=timeout,
        motion=motion,
        direction=direction,
        asset_id=m["asset_id"],
    )
    if out.exists():
        out.unlink()
    tmp.replace(out)
    return out


def _still_image_visual_segment(
    m: dict[str, Any],
    lp: Path,
    dur: float,
    *,
    settings: Any,
    gpu_cache_dir: Path,
    ffmpeg_bin: str,
    width: int,
    height: int,
    timeout: float,
) -> tuple[Any, ...]:
    """Build the timeline segment for a still, honoring the still_motion_renderer setting.

    - ``off`` → plain ``("image", path, dur)`` (static).
    - ``cpu`` → ``("image", path, dur, motion)`` so the frozen slideshow adds zoompan + keeps dissolves.
    - ``gpu`` → pre-render to an MP4 via the CUDA sidecar and return ``("video", mp4, dur)``.
    """
    from director_api.services.still_motion import pick_motion_for_asset, resolve_still_motion_renderer

    renderer = resolve_still_motion_renderer(settings)
    if renderer == "off":
        return ("image", lp, float(dur))
    motion, _direction = pick_motion_for_asset(m["asset_id"], settings)
    if motion == "none":
        return ("image", lp, float(dur))
    if renderer == "cpu":
        return ("image", lp, float(dur), motion)
    pre = _gpu_still_motion_mp4(
        m,
        lp,
        dur,
        settings=settings,
        cache_dir=gpu_cache_dir,
        ffmpeg_bin=ffmpeg_bin,
        width=width,
        height=height,
        timeout=timeout,
    )
    # Force exact slot duration at concat time (native NVENC length can drift from manifest).
    return ("video", pre, float(dur))


def _rough_cut_visual_segments_with_chapter_cards(
    db,
    manifest: list[dict[str, Any]],
    *,
    card_sec: float,
    storage_root: Path,
    ffprobe_bin: str = "ffprobe",
    settings: Any = None,
    gpu_cache_dir: Path | None = None,
    ffmpeg_bin: str = "ffmpeg",
    width: int = 1280,
    height: int = 720,
    timeout: float = 3600.0,
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
            if settings is not None and gpu_cache_dir is not None:
                segments.append(
                    _still_image_visual_segment(
                        m,
                        lp,
                        float(ds),
                        settings=settings,
                        gpu_cache_dir=gpu_cache_dir,
                        ffmpeg_bin=ffmpeg_bin,
                        width=width,
                        height=height,
                        timeout=timeout,
                    )
                )
            else:
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
    tenant = worker_tenant_id(job, payload)
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
    tenant = worker_tenant_id(job, payload)
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
    tenant = worker_tenant_id(job, payload)
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
    clip_xf = _timeline_clip_crossfade_sec(tj)
    visual_xf_pad = _visual_crossfade_pad_sec(settings, clip_xf)
    manifest_exp, slots_fc = _expand_manifest_and_slots_for_full_narration(
        db,
        manifest_fc,
        card_sec=card_sec,
        project_id=project_id,
        storage_root=storage_root,
        ffprobe_bin=ffprobe_bin,
        timeout_sec=float(settings.ffmpeg_timeout_sec),
        tail_padding_sec=scene_vo_tail_padding_sec_from_settings(settings),
        outro_tail_sec=_export_outro_music_tail_sec(settings),
        visual_crossfade_pad_sec=visual_xf_pad,
    )
    sum_o = _slots_total_duration(slots_orig)
    sum_e = _slots_total_duration(slots_fc)
    _drop_stale_fine_cut_if_mismatch(
        fine_p,
        expected_sec=sum_e,
        ffprobe_bin=ffprobe_bin,
        timeout_sec=float(settings.ffmpeg_timeout_sec),
    )
    # Prefer rough_cut for sync checks — fine_cut can hide an out-of-sync rough_cut.
    sync_probe = rough_p if path_is_readable_file(rough_p) else fine_p
    vid_len = (
        float(
            ffprobe_duration_seconds(
                sync_probe,
                ffprobe_bin=ffprobe_bin,
                timeout_sec=float(settings.ffmpeg_timeout_sec),
            )
        )
        if path_is_readable_file(sync_probe)
        else 0.0
    )
    want_visual_recompile = (sum_e > sum_o + 0.05) or (
        path_is_readable_file(sync_probe) and abs(sum_e - vid_len) > 0.25
    )
    need_visual_recompile = False
    still_motion_cache_dir = rough_p.parent / ".still_motion"
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
        from director_api.services.still_motion import resolve_still_motion_renderer

        if resolve_still_motion_renderer(settings) == "gpu":
            _clear_gpu_still_motion_cache(still_motion_cache_dir)
        # ``manifest_exp`` may pad each visual clip by the crossfade overlap when dissolves are
        # used (CPU stills). GPU stills skip that pad (hard cuts). Recompile so video length
        # matches the narration stem.
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
    else:
        _drop_stale_fine_cut_if_mismatch(
            fine_p,
            expected_sec=sum_e,
            ffprobe_bin=ffprobe_bin,
            timeout_sec=float(settings.ffmpeg_timeout_sec),
        )
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
    from director_api.services.publish_youtube import resolve_publish_to_youtube, try_youtube_upload_after_export

    payload_po: dict[str, Any] | None = None
    if (payload or {}).get("publish_to_youtube") is not None:
        payload_po = {"publish_to_youtube": payload.get("publish_to_youtube")}
    publish_flag = resolve_publish_to_youtube(project, payload_po)
    youtube_upload = try_youtube_upload_after_export(
        db,
        settings,
        tenant_id=tenant,
        project=project,
        publish_to_youtube=publish_flag,
        timeline_version_id=tv_id,
    )
    try:
        from director_api.services.scene_precompile_enqueue import cancel_project_scene_precompile_backlog

        cancel_project_scene_precompile_backlog(
            db,
            tenant_id=tenant,
            project_id=project_id,
            reason="cancelled_after_final_cut",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("scene_precompile_backlog_cancel_failed", stage="final_cut", error=str(e)[:200])
    return {
        "timeline_version_id": str(tv.id),
        "output_url": tv.output_url,
        "mux": mux_meta,
        "youtube_upload": youtube_upload,
    }


def _export_bundle(db, job: Job, settings: Any) -> dict[str, Any]:
    payload = job.payload or {}
    tv_id = uuid.UUID(str(payload["timeline_version_id"]))
    project_id = uuid.UUID(str(payload["project_id"]))
    tenant = worker_tenant_id(job, payload)
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
    clip_crossfade_override: float | None = None,
) -> dict[str, Any]:
    payload = job.payload or {}
    tv_id = uuid.UUID(str(payload["timeline_version_id"]))
    tenant = worker_tenant_id(job, payload)
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
    clip_xf = (
        max(0.0, float(clip_crossfade_override))
        if clip_crossfade_override is not None
        else _timeline_clip_crossfade_sec(tj)
    )
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
            still_motion_cache_dir = out_path.parent / ".still_motion"
            card_sec = _export_chapter_title_card_sec(settings)
            if card_sec > 0:
                mixed_segments = _rough_cut_visual_segments_with_chapter_cards(
                    db,
                    manifest,
                    card_sec=card_sec,
                    storage_root=storage_root,
                    ffprobe_bin=ffprobe_bin,
                    settings=settings,
                    gpu_cache_dir=still_motion_cache_dir,
                    ffmpeg_bin=ffmpeg_bin,
                    width=ew,
                    height=eh,
                    timeout=float(settings.ffmpeg_timeout_sec),
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
                        mixed_segments.append(
                            _still_image_visual_segment(
                                m,
                                lp,
                                float(ds),
                                settings=settings,
                                gpu_cache_dir=still_motion_cache_dir,
                                ffmpeg_bin=ffmpeg_bin,
                                width=ew,
                                height=eh,
                                timeout=float(settings.ffmpeg_timeout_sec),
                            )
                        )
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
                from director_api.services.still_motion import (
                    pick_motion_for_asset,
                    resolve_still_motion_renderer,
                )

                _renderer = resolve_still_motion_renderer(settings)
                slides = []
                slide_motions = []
                image_rows = []
                for m in manifest:
                    lp = path_from_storage_url(m.get("storage_url"), storage_root=storage_root)
                    if lp is None or not path_is_readable_file(lp):
                        raise ValueError(f"missing local image file for asset {m.get('asset_id')}")
                    ds = m.get("duration_sec")
                    if ds is None or float(ds) <= 0:
                        raise ValueError(f"invalid duration_sec for image asset {m.get('asset_id')}")
                    slides.append((lp, float(ds)))
                    slide_motions.append(pick_motion_for_asset(m["asset_id"], settings)[0])
                    image_rows.append((m, lp, float(ds)))
                if _renderer == "gpu":
                    # GPU stills are pre-rendered to MP4 and concatenated (hard cuts between them).
                    gpu_segments = [
                        _still_image_visual_segment(
                            m,
                            lp,
                            ds,
                            settings=settings,
                            gpu_cache_dir=still_motion_cache_dir,
                            ffmpeg_bin=ffmpeg_bin,
                            width=ew,
                            height=eh,
                            timeout=float(settings.ffmpeg_timeout_sec),
                        )
                        for (m, lp, ds) in image_rows
                    ]
                    compile_meta = compile_mixed_visual_timeline(
                        gpu_segments,
                        out_path,
                        width=ew,
                        height=eh,
                        ffmpeg_bin=ffmpeg_bin,
                        ffprobe_bin=ffprobe_bin,
                        timeout_sec=float(settings.ffmpeg_timeout_sec),
                        image_batch_crossfade_sec=0.0,
                    )
                    compile_meta["still_motion_renderer"] = "gpu"
                else:
                    use_motions = _renderer == "cpu" and any(x in ("pan", "zoom") for x in slide_motions)
                    compile_meta = compile_image_slideshow(
                        slides,
                        out_path,
                        width=ew,
                        height=eh,
                        ffmpeg_bin=ffmpeg_bin,
                        timeout_sec=float(settings.ffmpeg_timeout_sec),
                        motion="none",
                        slide_motions=slide_motions if use_motions else None,
                        crossfade_sec=clip_xf,
                        slow_zoom=False,
                    )
                    compile_meta["still_motion_renderer"] = _renderer if use_motions else "off"
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
    try:
        from director_api.services.scene_precompile_enqueue import cancel_project_scene_precompile_backlog

        cancel_project_scene_precompile_backlog(
            db,
            tenant_id=tenant,
            project_id=project_id,
            reason="cancelled_after_rough_cut",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("scene_precompile_backlog_cancel_failed", stage="rough_cut", error=str(e)[:200])
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
    tenant = worker_tenant_id(job, payload)
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


def _rough_cut_apply_precompiled_segments(
    mixed_segments: list[Any],
    manifest: list[dict[str, Any]],
    *,
    storage_root: Path,
    project_id: uuid.UUID,
) -> list[Any]:
    from director_api.services.scene_precompile import substitute_precompiled_clip_segments

    seg2, substituted = substitute_precompiled_clip_segments(
        mixed_segments,
        manifest,
        storage_root=storage_root,
        project_id=project_id,
    )
    if substituted:
        log.info(
            "rough_cut_precompiled_segments",
            project_id=str(project_id),
            substituted=substituted,
            manifest_clips=len(manifest),
        )
    return seg2


def _scene_precompile(db, job: Job, settings: Any) -> dict[str, Any]:
    """Encode one scene image/video asset to export-sized MP4 for faster timeline compiles."""
    from director_api.services.scene_precompile import (
        asset_source_fingerprint,
        compile_asset_precompile,
        precompile_is_current,
        precompile_storage_fingerprint_for_asset,
        write_precompile_meta,
    )

    payload = job.payload or {}
    asset_id = uuid.UUID(str(payload["asset_id"]))
    project_id = uuid.UUID(str(payload["project_id"]))
    tenant = worker_tenant_id(job, payload)
    asset = db.get(Asset, asset_id)
    if not asset or asset.project_id != project_id or asset.tenant_id != tenant:
        raise ValueError("asset not found for precompile")
    project = db.get(Project, project_id)
    if not project or project.tenant_id != tenant:
        raise ValueError("project not found")
    if asset.asset_type not in ("image", "video") or asset.status != "succeeded":
        return {"skipped": True, "reason": "asset_not_ready"}

    storage_root = Path(settings.local_storage_root).resolve()
    duration_sec = float(payload.get("duration_sec") or 0)
    if duration_sec <= 0:
        from director_api.services.scene_precompile import default_duration_sec_for_asset

        duration_sec = default_duration_sec_for_asset(
            asset, settings, storage_root=storage_root
        )
    from director_api.services.still_motion import motion_signature

    motion_sig = motion_signature(settings) if asset.asset_type == "image" else ""
    fp = str(payload.get("fingerprint") or "").strip() or precompile_storage_fingerprint_for_asset(
        asset
    )
    if precompile_is_current(
        storage_root=storage_root,
        project_id=project_id,
        asset_id=asset.id,
        fingerprint=fp,
        clip_duration_sec=duration_sec,
        motion_sig=motion_sig or None,
    ):
        return {"skipped": True, "reason": "already_current"}

    ew, eh = _project_export_dimensions(project)
    out = compile_asset_precompile(
        storage_root=storage_root,
        project_id=project_id,
        asset=asset,
        duration_sec=duration_sec,
        width=ew,
        height=eh,
        settings=settings,
    )
    write_precompile_meta(
        storage_root=storage_root,
        project_id=project_id,
        asset=asset,
        fingerprint=fp,
        duration_sec=duration_sec,
        width=ew,
        height=eh,
        motion_sig=motion_sig,
    )
    return {
        "ok": True,
        "asset_id": str(asset.id),
        "scene_id": str(asset.scene_id) if asset.scene_id else None,
        "output_path": str(out),
        "fingerprint": fp,
    }

