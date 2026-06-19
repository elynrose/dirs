"""Recover worker_tasks.py after a bad phase5 extraction."""
from pathlib import Path

TASKS = Path(__file__).resolve().parents[1] / "director_api" / "tasks"
p5 = (TASKS / "phase5_compile_impl.py").read_text(encoding="utf-8").splitlines(keepends=True)
wt_header = (TASKS / "worker_tasks.py").read_text(encoding="utf-8").splitlines(keepends=True)

# phase5 only: lines 213-1387 and 4407-5527 (1-based)
phase5_part1 = p5[212:1387]
phase5_part2 = p5[4406:5527]

p5_header = '''"""Phase 5 compile, narration, subtitles, and export job bodies."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import desc, func, select
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
from director_api.services import phase3 as phase3_svc
from director_api.services.phase5_readiness import Phase5GateError, raise_phase5_gate
from director_api.services.project_frame import coerce_frame_aspect_ratio, frame_pixel_size
from director_api.services.research_service import sanitize_jsonb_text
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.services.scene_timeline_duration import scene_vo_tail_padding_sec_from_settings
from director_api.services.subtitles_vtt import assemble_project_subtitle_markdown, script_to_webvtt
from director_api.services.timeline_image_repair import list_export_ready_scene_visuals_ordered
from director_api.services.timeline_manifest_prefetch import manifest_prefetch_asset_hierarchy
from director_api.storage.filesystem import FilesystemStorage
from director_api.timeline_mix_levels import mix_music_volume_from_timeline, mix_narration_volume_from_timeline
from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export
from director_api.tasks.worker_helpers import _record_usage, _worker_runtime_for_job
from director_api.logging_config import get_logger
from ffmpeg_pipelines.audio_concat import concat_audio_files
from ffmpeg_pipelines.audio_slot import normalize_audio_segment_to_duration
from ffmpeg_pipelines.encode import VideoEncodeConfig, video_encode_config_from_settings
from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.export_manifest import build_export_manifest
from ffmpeg_pipelines.ffmpeg_tracked import ExportFfmpegRegistry
from ffmpeg_pipelines.mixed_timeline import compile_mixed_visual_timeline
from ffmpeg_pipelines.mux_master import mux_video_with_narration_and_music
from ffmpeg_pipelines.overlay_video import burn_overlays_on_video
from ffmpeg_pipelines.paths import mkdir_parent, path_from_storage_url, path_is_readable_file, path_stat
from ffmpeg_pipelines.probe import ffprobe_duration_seconds
from ffmpeg_pipelines.slideshow import compile_image_slideshow
from ffmpeg_pipelines.still_to_video import encode_image_to_mp4

log = get_logger(__name__)

StillMotion = Literal["none", "pan", "zoom"]

_FRAMING_SAFETY_NEG = (
    "cropped head, cut off head, cut off feet, cut off hands, out of frame, partial body, "
    "amputated limbs, missing limbs"
)

'''

(TASKS / "phase5_compile_impl.py").write_text(
    p5_header + "".join(phase5_part1) + "".join(phase5_part2),
    encoding="utf-8",
)

# agent + celery: everything in old p5 except phase5 parts, minus duplicate header lines 1-211
agent_chunks = []
agent_chunks.extend(p5[1387:1521])  # through strict_research_gate end
agent_chunks.extend(p5[1533:4406])  # skip embedded adapter/phase2 celery (1522-1532)
agent_chunks.extend(p5[5527:])  # anything after fine_cut if present

# Rebuild worker_tasks: header through phase5 import, then agent body, then celery tail from current wt
wt = wt_header
# find phase5 import block end
end_import = 0
for i, line in enumerate(wt):
    if line.strip() == ")":
        if i > 340 and "phase5_compile_impl" in wt[i - 5]:
            end_import = i + 1
            break

new_wt = wt[:end_import] + ["\n"] + agent_chunks
# ensure celery tasks at end
celery_tail = []
seen = set()
for line in wt[end_import:]:
    if "@celery_app.task" in line or line.startswith("def run_"):
        celery_tail.append(line)
    elif celery_tail and (line.startswith("    ") or line.strip() == "" or line.startswith("def ")):
        celery_tail.append(line)
    elif celery_tail and line.startswith("#"):
        celery_tail.append(line)

# Dedupe: use full celery section from corrupted file for run_agent, run_phase3, adapter, phase2
for block_start in [1521, 4149, 3275]:
    if block_start < len(p5):
        pass

# Append known celery decorators from corrupted file
celery_sections = []
idx = 0
while idx < len(p5):
    if p5[idx].startswith("@celery_app.task"):
        start = idx
        while idx < len(p5) and not (idx > start and p5[idx].startswith("@celery_app.task")):
            idx += 1
            if idx >= len(p5):
                break
        celery_sections.append("".join(p5[start:idx]))
    else:
        idx += 1

# worker_tasks should have: header, agent code, all celery tasks
existing_celery = "".join(wt[end_import:])
if "run_agent_run" not in existing_celery:
    agent_chunks.extend([s for s in celery_sections if "run_agent_run" in s or "run_phase3" in s or "run_adapter" in s or "run_phase2" in s])

new_wt = wt[:end_import] + ["\n"] + agent_chunks
if "run_agent_run" not in "".join(new_wt):
    new_wt.extend([s for s in celery_sections if "run_agent_run" in s])
if "run_phase3_job" not in "".join(new_wt):
    new_wt.extend([s for s in celery_sections if "run_phase3_job" in s])
if "run_adapter_smoke" not in "".join(new_wt):
    new_wt.extend([s for s in celery_sections if "run_adapter_smoke" in s])
if "run_phase2_job" not in "".join(new_wt):
    new_wt.extend([s for s in celery_sections if "run_phase2_job" in s])

new_wt.extend(wt[end_import:])
(TASKS / "worker_tasks.py").write_text("".join(new_wt), encoding="utf-8")
print("recovered", len(agent_chunks), "agent lines; phase5", len(phase5_part1) + len(phase5_part2))
