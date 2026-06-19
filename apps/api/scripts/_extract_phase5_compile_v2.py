"""One-shot: extract phase5 compile bodies from worker_runtime (+ 2 helpers from worker_tasks)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WR = ROOT / "director_api/tasks/worker_runtime.py"
WT = ROOT / "director_api/tasks/worker_tasks.py"
OUT = ROOT / "director_api/tasks/phase5_compile_impl.py"

NAMES_FROM_WR = [
    "_export_chapter_title_card_sec",
    "_timeline_clip_crossfade_sec",
    "_build_timeline_export_manifest",
    "_manifest_row_duration_sec",
    "_final_cut_audio_slots_from_manifest",
    "_slots_total_duration",
    "_expand_manifest_and_slots_for_full_narration",
    "_count_scene_narration_tracks",
    "_latest_chapter_narration_audio_path",
    "_build_scene_timeline_narration_stem",
    "_rough_cut_visual_segments_with_chapter_cards",
    "_rough_cut_video_segment_tuple",
    "_bind_asset_local_file",
    "_narration_generate",
    "_narration_generate_scene",
    "_subtitles_generate",
    "_attach_latest_music_bed_if_missing",
    "_final_cut",
    "_export_bundle",
    "_append_timeline_export_warnings",
    "_rough_cut",
    "_fine_cut",
]

NAMES_FROM_WT = [
    "_rough_cut_apply_precompiled_segments",
    "_scene_precompile",
]

HEADER = '''"""Phase 5 compile, narration, and export manifest helpers (canonical)."""

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
from director_api.storage.filesystem import FilesystemStorage
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

from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export

log = get_logger(__name__)

'''


def extract_named_functions(path: Path, names: set[str]) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            chunk = "".join(lines[node.lineno - 1 : node.end_lineno])
            out[node.name] = chunk
    missing = names - set(out)
    if missing:
        raise SystemExit(f"missing in {path.name}: {sorted(missing)}")
    return out


def remove_functions(path: Path, names: set[str]) -> None:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    remove_ranges: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            remove_ranges.append((node.lineno - 1, node.end_lineno))
    if not remove_ranges:
        return
    remove_ranges.sort()
    new_lines: list[str] = []
    idx = 0
    for start, end in remove_ranges:
        new_lines.extend(lines[idx:start])
        idx = end
    new_lines.extend(lines[idx:])
    path.write_text("".join(new_lines), encoding="utf-8")


def insert_import_after_log(path: Path, import_block: str) -> None:
    text = path.read_text(encoding="utf-8")
    marker = "log = get_logger(__name__)\n"
    if import_block.strip() in text:
        return
    if marker not in text:
        raise SystemExit(f"marker not found in {path.name}")
    text = text.replace(marker, marker + "\n" + import_block + "\n", 1)
    path.write_text(text, encoding="utf-8")


def patch_worker_runtime_scene_precompile_import(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = "from director_api.tasks.worker_tasks import _scene_precompile"
    new = "from director_api.tasks.phase5_compile_impl import _scene_precompile"
    if old in text:
        text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


def main() -> None:
    wr_funcs = extract_named_functions(WR, set(NAMES_FROM_WR))
    wt_funcs = extract_named_functions(WT, set(NAMES_FROM_WT))

    body_parts = [wr_funcs[n] for n in NAMES_FROM_WR]
    body_parts.extend(wt_funcs[n] for n in NAMES_FROM_WT)
    OUT.write_text(HEADER + "\n\n".join(body_parts) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")

    remove_functions(WR, set(NAMES_FROM_WR))
    remove_functions(WT, set(NAMES_FROM_WR) | set(NAMES_FROM_WT))
    print("removed function bodies from worker_runtime and worker_tasks")

    import_names = sorted(set(NAMES_FROM_WR) | set(NAMES_FROM_WT))
    import_block = (
        "from director_api.tasks.phase5_compile_impl import (\n"
        + "".join(f"    {n},\n" for n in import_names)
        + ")\n"
    )
    insert_import_after_log(WR, import_block)
    insert_import_after_log(WT, import_block)
    patch_worker_runtime_scene_precompile_import(WR)
    print("patched imports")


if __name__ == "__main__":
    main()
