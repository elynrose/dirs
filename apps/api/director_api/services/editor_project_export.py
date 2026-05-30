"""Export Directely timelines for external editors (CapCut, OpenShot).

Produces a ZIP with:
- ``media/`` — scene clips (copied with stable names)
- ``openshot/directely_fcpxml.xml`` — Final Cut Pro 7 XML (import in OpenShot: File → Import XML)
- ``capcut/<draft>/draft_content.json`` + ``capcut/<draft>/assets/`` — CapCut desktop draft folder
- ``README.txt`` — import steps
"""

from __future__ import annotations

import json
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from xml.sax.saxutils import escape

from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file
from ffmpeg_pipelines.probe import ffprobe_duration_seconds
from sqlalchemy.orm import Session

from director_api.db.models import MusicBed, NarrationTrack, Project, Scene, TimelineVersion
from director_api.services.phase5_readiness import get_timeline_asset_for_project
from director_api.services.project_frame import coerce_frame_aspect_ratio, frame_pixel_size
from director_api.services.scene_timeline_duration import get_export_narration_budget_sec_for_scene
from director_api.services.timeline_manifest_prefetch import manifest_prefetch_asset_hierarchy
from director_api.validation.timeline_schema import validate_timeline_document

_TIMEBASE = 30


@dataclass
class EditorClip:
    rel_media: str
    abs_path: Path
    label: str
    asset_type: str
    timeline_start_sec: float
    duration_sec: float
    trim_start_sec: float
    scene_id: str | None = None


@dataclass
class EditorAudioClip:
    rel_media: str
    abs_path: Path
    label: str
    timeline_start_sec: float
    duration_sec: float


@dataclass
class EditorExportPlan:
    project_title: str
    width: int
    height: int
    fps: int
    ratio_label: str
    video_clips: list[EditorClip] = field(default_factory=list)
    narration_clips: list[EditorAudioClip] = field(default_factory=list)
    music_clip: EditorAudioClip | None = None
    total_duration_sec: float = 0.0


def _sec_to_frames(sec: float) -> int:
    return max(1, int(round(float(sec) * _TIMEBASE)))


def _new_id() -> str:
    return str(uuid.uuid4())


def _scene_clip_duration_sec(settings: Any) -> float:
    return float(getattr(settings, "scene_clip_duration_sec", 10) or 10)


def _build_timeline_export_manifest(
    db: Session,
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
    raise ValueError(f"unsupported asset_type: {at}")


def _safe_filename(name: str, fallback: str) -> str:
    base = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name or "").strip())[:80]
    return base or fallback


def build_editor_export_plan(
    db: Session,
    project: Project,
    tv: TimelineVersion,
    settings: Any,
    *,
    allow_unapproved_media: bool = False,
) -> EditorExportPlan:
    """Resolve timeline manifest into clip rows with on-disk media paths."""
    storage_root = Path(settings.local_storage_root).resolve()
    ffprobe_bin = (getattr(settings, "ffprobe_bin", None) or "ffprobe").strip() or "ffprobe"
    timeout = float(getattr(settings, "ffmpeg_timeout_sec", 120) or 120)

    w, h = frame_pixel_size(coerce_frame_aspect_ratio(getattr(project, "frame_aspect_ratio", None)))
    ratio = "9:16" if w < h else "16:9"

    manifest = _build_timeline_export_manifest(
        db, project, tv, settings, allow_unapproved_media=allow_unapproved_media
    )
    asset_by_id, scene_by_id, _ch_by_id = manifest_prefetch_asset_hierarchy(db, manifest)

    video_clips: list[EditorClip] = []
    timeline_pos = 0.0
    voice_used: set[uuid.UUID] = set()

    for idx, m in enumerate(manifest):
        aid = uuid.UUID(str(m["asset_id"]))
        asset = asset_by_id.get(aid)
        lp = path_from_storage_url(m.get("storage_url"), storage_root=storage_root)
        if lp is None or not path_is_readable_file(lp):
            raise ValueError(f"missing media file for asset {aid}")

        dur = _manifest_row_duration_sec(
            m, storage_root=storage_root, ffprobe_bin=ffprobe_bin, timeout_sec=timeout
        )
        trim_start = float(m.get("trim_start_sec") or 0)
        ext = lp.suffix.lower() or (".png" if str(m.get("asset_type")) == "image" else ".mp4")
        rel = f"media/{idx:03d}_{_safe_filename(lp.stem, 'clip')}{ext}"

        sid: uuid.UUID | None = asset.scene_id if asset else None
        if sid and sid not in voice_used:
            voice_used.add(sid)
            narr = get_export_narration_budget_sec_for_scene(
                db,
                project_id=project.id,
                scene_id=sid,
                storage_root=storage_root,
                ffprobe_bin=ffprobe_bin,
                timeout_sec=timeout,
            )
            if narr is not None and narr > dur:
                dur = float(narr)

        sc = scene_by_id.get(sid) if sid else None
        label = f"Scene {sc.order_index}" if sc else f"Clip {idx + 1}"

        video_clips.append(
            EditorClip(
                rel_media=rel,
                abs_path=lp,
                label=label,
                asset_type=str(m.get("asset_type") or "video"),
                timeline_start_sec=timeline_pos,
                duration_sec=dur,
                trim_start_sec=trim_start,
                scene_id=str(sid) if sid else None,
            )
        )
        timeline_pos += dur

    narration_clips: list[EditorAudioClip] = []
    narr_pos = 0.0
    for vc in video_clips:
        if not vc.scene_id:
            narr_pos = vc.timeline_start_sec + vc.duration_sec
            continue
        sid = uuid.UUID(vc.scene_id)
        nt = (
            db.query(NarrationTrack)
            .filter(
                NarrationTrack.project_id == project.id,
                NarrationTrack.scene_id == sid,
                NarrationTrack.audio_url.isnot(None),
            )
            .order_by(NarrationTrack.created_at.desc())
            .first()
        )
        if not nt or not str(nt.audio_url or "").strip():
            narr_pos = vc.timeline_start_sec + vc.duration_sec
            continue
        np = path_from_storage_url(nt.audio_url, storage_root=storage_root)
        if np is None or not path_is_readable_file(np):
            narr_pos = vc.timeline_start_sec + vc.duration_sec
            continue
        ndur = float(
            ffprobe_duration_seconds(np, ffprobe_bin=ffprobe_bin, timeout_sec=min(timeout, 120.0))
        )
        if ndur <= 0:
            narr_pos = vc.timeline_start_sec + vc.duration_sec
            continue
        rel = f"media/narration_{vc.scene_id[:8]}{np.suffix.lower() or '.mp3'}"
        narration_clips.append(
            EditorAudioClip(
                rel_media=rel,
                abs_path=np,
                label=f"Narration {vc.label}",
                timeline_start_sec=vc.timeline_start_sec,
                duration_sec=ndur,
            )
        )
        narr_pos = vc.timeline_start_sec + vc.duration_sec

    music_clip: EditorAudioClip | None = None
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    mb_ref = tj.get("music_bed_id")
    if mb_ref:
        try:
            mb = db.get(MusicBed, uuid.UUID(str(mb_ref)))
        except (ValueError, TypeError):
            mb = None
        if mb and mb.storage_url:
            mp = path_from_storage_url(mb.storage_url, storage_root=storage_root)
            if mp and path_is_readable_file(mp):
                mdur = float(
                    ffprobe_duration_seconds(mp, ffprobe_bin=ffprobe_bin, timeout_sec=min(timeout, 120.0))
                )
                if mdur <= 0:
                    mdur = timeline_pos
                music_clip = EditorAudioClip(
                    rel_media=f"media/music_bed{mp.suffix.lower() or '.mp3'}",
                    abs_path=mp,
                    label="Music bed",
                    timeline_start_sec=0.0,
                    duration_sec=min(mdur, timeline_pos) if timeline_pos > 0 else mdur,
                )

    return EditorExportPlan(
        project_title=str(project.title or "Directely project"),
        width=w,
        height=h,
        fps=_TIMEBASE,
        ratio_label=ratio,
        video_clips=video_clips,
        narration_clips=narration_clips,
        music_clip=music_clip,
        total_duration_sec=timeline_pos,
    )


def build_fcpxml(plan: EditorExportPlan, *, media_prefix: str = "../media/") -> str:
    """Final Cut Pro 7 XML (xmeml) — importable in OpenShot."""
    total_frames = _sec_to_frames(plan.total_duration_sec)
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE xmeml>',
        '<xmeml version="4">',
        "  <sequence>",
        f"    <name>{escape(plan.project_title[:200])}</name>",
        f"    <duration>{total_frames}</duration>",
        "    <rate>",
        f"      <timebase>{_TIMEBASE}</timebase>",
        "      <ntsc>FALSE</ntsc>",
        "    </rate>",
        "    <media>",
        "      <video>",
        "        <format>",
        "          <samplecharacteristics>",
        f"            <width>{plan.width}</width>",
        f"            <height>{plan.height}</height>",
        "          </samplecharacteristics>",
        "        </format>",
        "        <track>",
    ]

    file_ids: dict[str, str] = {}

    def file_id_for(rel: str) -> str:
        if rel not in file_ids:
            file_ids[rel] = f"file-{len(file_ids) + 1}"
        return file_ids[rel]

    for i, clip in enumerate(plan.video_clips):
        fid = file_id_for(clip.rel_media)
        path_url = PurePosixPath(media_prefix, Path(clip.rel_media).name).as_posix()
        dur_f = _sec_to_frames(clip.duration_sec)
        start_f = _sec_to_frames(clip.timeline_start_sec)
        in_f = _sec_to_frames(clip.trim_start_sec)
        out_f = in_f + dur_f
        lines.extend(
            [
                "          <clipitem>",
                f"            <name>{escape(clip.label)}</name>",
                f"            <duration>{dur_f}</duration>",
                f"            <start>{start_f}</start>",
                f"            <end>{start_f + dur_f}</end>",
                f"            <in>{in_f}</in>",
                f"            <out>{out_f}</out>",
                f'            <file id="{fid}">',
                f"              <name>{escape(Path(clip.rel_media).name)}</name>",
                f"              <pathurl>{escape(path_url)}</pathurl>",
                "            </file>",
                "          </clipitem>",
            ]
        )

    lines.extend(["        </track>", "      </video>", "      <audio>"])

    def audio_track(clips: list[EditorAudioClip], track_name: str) -> None:
        lines.append("        <track>")
        for clip in clips:
            fid = file_id_for(clip.rel_media)
            path_url = PurePosixPath(media_prefix, Path(clip.rel_media).name).as_posix()
            dur_f = _sec_to_frames(clip.duration_sec)
            start_f = _sec_to_frames(clip.timeline_start_sec)
            lines.extend(
                [
                    "          <clipitem>",
                    f"            <name>{escape(clip.label or track_name)}</name>",
                    f"            <duration>{dur_f}</duration>",
                    f"            <start>{start_f}</start>",
                    f"            <end>{start_f + dur_f}</end>",
                    f"            <in>0</in>",
                    f"            <out>{dur_f}</out>",
                    f'            <file id="{fid}">',
                    f"              <name>{escape(Path(clip.rel_media).name)}</name>",
                    f"              <pathurl>{escape(path_url)}</pathurl>",
                    "            </file>",
                    "          </clipitem>",
                ]
            )
        lines.append("        </track>")

    if plan.narration_clips:
        audio_track(plan.narration_clips, "Narration")
    if plan.music_clip:
        audio_track([plan.music_clip], "Music")

    lines.extend(
        [
            "      </audio>",
            "    </media>",
            "  </sequence>",
            "</xmeml>",
        ]
    )
    return "\n".join(lines) + "\n"


def build_capcut_draft(plan: EditorExportPlan, *, assets_rel_prefix: str = "assets/") -> dict[str, Any]:
    """Minimal CapCut ``draft_content.json`` (CapCut International / app_source cc)."""
    draft_id = _new_id()
    duration_us = int(round(plan.total_duration_sec * 1_000_000))
    video_materials: list[dict[str, Any]] = []
    audio_materials: list[dict[str, Any]] = []
    video_segments: list[dict[str, Any]] = []
    audio_segments: list[dict[str, Any]] = []

    for clip in plan.video_clips:
        mat_id = _new_id()
        seg_id = _new_id()
        dur_us = int(round(clip.duration_sec * 1_000_000))
        start_us = int(round(clip.timeline_start_sec * 1_000_000))
        trim_us = int(round(clip.trim_start_sec * 1_000_000))
        asset_name = Path(clip.rel_media).name
        is_photo = clip.asset_type == "image"
        video_materials.append(
            {
                "id": mat_id,
                "type": "photo" if is_photo else "video",
                "path": f"{assets_rel_prefix}{asset_name}",
                "duration": dur_us,
                "width": plan.width,
                "height": plan.height,
                "material_name": clip.label,
            }
        )
        video_segments.append(
            {
                "id": seg_id,
                "material_id": mat_id,
                "target_timerange": {"start": start_us, "duration": dur_us},
                "source_timerange": {"start": trim_us, "duration": dur_us},
                "extra_material_refs": [],
                "clip": {"rotation": 0.0, "alpha": 1.0},
                "speed": 1.0,
                "volume": 1.0,
                "visible": True,
            }
        )

    for clip in plan.narration_clips + ([plan.music_clip] if plan.music_clip else []):
        if clip is None:
            continue
        mat_id = _new_id()
        seg_id = _new_id()
        dur_us = int(round(clip.duration_sec * 1_000_000))
        start_us = int(round(clip.timeline_start_sec * 1_000_000))
        asset_name = Path(clip.rel_media).name
        audio_materials.append(
            {
                "id": mat_id,
                "type": "extract_music",
                "path": f"{assets_rel_prefix}{asset_name}",
                "duration": dur_us,
                "name": clip.label,
            }
        )
        audio_segments.append(
            {
                "id": seg_id,
                "material_id": mat_id,
                "target_timerange": {"start": start_us, "duration": dur_us},
                "source_timerange": {"start": 0, "duration": dur_us},
                "extra_material_refs": [],
                "clip": None,
                "speed": 1.0,
                "volume": 1.0,
                "visible": True,
            }
        )

    tracks: list[dict[str, Any]] = [
        {
            "id": _new_id(),
            "type": "video",
            "name": "Directely video",
            "segments": video_segments,
        },
    ]
    if audio_segments:
        tracks.append(
            {
                "id": _new_id(),
                "type": "audio",
                "name": "Directely audio",
                "segments": audio_segments,
            }
        )

    return {
        "id": draft_id,
        "name": plan.project_title[:120],
        "duration": duration_us,
        "fps": plan.fps,
        "canvas_config": {
            "width": plan.width,
            "height": plan.height,
            "ratio": plan.ratio_label,
        },
        "platform": {
            "app_source": "cc",
            "app_version": "9.0.0",
            "os": "windows",
        },
        "tracks": tracks,
        "materials": {
            "videos": video_materials,
            "audios": audio_materials,
            "texts": [],
            "stickers": [],
            "video_effects": [],
            "material_animations": [],
            "transitions": [],
            "speeds": [],
            "audio_fades": [],
            "placeholder_infos": [],
        },
        "extra_info": {},
        "free_render_index_mode_on": False,
    }


def write_editor_export_zip(
    plan: EditorExportPlan,
    dest_zip: Path,
    *,
    capcut_folder_name: str = "directely_capcut",
) -> None:
    """Write ZIP at ``dest_zip`` (overwrites if present)."""
    readme = f"""Directely editor export — {plan.project_title}

CONTENTS
- media/     All scene clips and audio used on the timeline
- openshot/  Import into OpenShot (see below)
- capcut/    CapCut desktop draft folder (see below)

OPENSHOT
1. Unzip this archive and keep the folder structure intact.
2. In OpenShot: File → Import → Final Cut Pro XML…
3. Choose: openshot/directely_fcpxml.xml
4. If prompted for missing files, point to the media/ folder next to openshot/.

CAPCUT (desktop)
1. Close CapCut if it is open.
2. Copy the folder capcut/{capcut_folder_name}/ into your CapCut projects directory:
   Windows: %LOCALAPPDATA%\\CapCut\\User Data\\Projects\\com.lveditor.draft\\
   macOS:   ~/Movies/CapCut/User Data/Projects/com.lveditor.draft/
3. Open CapCut and open the project "{plan.project_title}" from the project list.
   (If it does not appear, use CapCut → Import and select capcut/{capcut_folder_name}/draft_content.json
    after copying assets/ beside it.)

Canvas: {plan.width}×{plan.height} ({plan.ratio_label}), {plan.fps} fps, ~{plan.total_duration_sec:.1f}s timeline.
"""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    capcut_root = f"capcut/{capcut_folder_name}"
    capcut_assets = f"{capcut_root}/assets"

    media_paths: dict[str, Path] = {}
    for clip in plan.video_clips:
        media_paths[clip.rel_media] = clip.abs_path
    for clip in plan.narration_clips:
        media_paths[clip.rel_media] = clip.abs_path
    if plan.music_clip:
        media_paths[plan.music_clip.rel_media] = plan.music_clip.abs_path

    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", readme)
        zf.writestr("openshot/IMPORT.txt", "Import directely_fcpxml.xml via File → Import → Final Cut Pro XML in OpenShot.\n")
        zf.writestr("openshot/directely_fcpxml.xml", build_fcpxml(plan))

        draft = build_capcut_draft(plan)
        zf.writestr(f"{capcut_root}/draft_content.json", json.dumps(draft, indent=2, ensure_ascii=False))

        for rel, abs_path in media_paths.items():
            zf.writestr(rel, abs_path.read_bytes())
            capcut_name = Path(rel).name
            zf.writestr(f"{capcut_assets}/{capcut_name}", abs_path.read_bytes())
