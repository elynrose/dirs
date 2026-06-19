"""One-off: extract phase5 compile from worker_tasks to phase5_compile_impl.py."""
from pathlib import Path

TASKS = Path(__file__).resolve().parents[1] / "director_api" / "tasks"
wt = TASKS / "worker_tasks.py"
lines = wt.read_text(encoding="utf-8").splitlines(keepends=True)
start, end = 344, 5659
body = "".join(lines[start:end])
header = wt.read_text(encoding="utf-8").split("from director_api.tasks.phase3_impl")[0]
header += """from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export
from director_api.tasks.worker_helpers import (
    _record_usage,
    _worker_runtime_for_job,
)
from director_api.services.project_frame import frame_pixel_size, coerce_frame_aspect_ratio
from director_api.logging_config import get_logger

log = get_logger(__name__)

StillMotion = Literal["none", "pan", "zoom"]

"""
(TASKS / "phase5_compile_impl.py").write_text(header + body, encoding="utf-8")
new_lines = lines[:start] + [
    "# Phase 5 compile bodies live in phase5_compile_impl.py\n",
    "from director_api.tasks.phase5_compile_impl import (\n",
    "    _build_timeline_export_manifest,\n",
    "    _export_bundle,\n",
    "    _export_chapter_title_card_sec,\n",
    "    _ffmpeg_video_encode_config,\n",
    "    _final_cut,\n",
    "    _fine_cut,\n",
    "    _manifest_requires_still_motion_encode,\n",
    "    _narration_generate,\n",
    "    _narration_generate_scene,\n",
    "    _resolve_still_motion,\n",
    "    _rough_cut,\n",
    "    _rough_cut_apply_precompiled_segments,\n",
    "    _scene_precompile,\n",
    "    _subtitles_generate,\n",
    ")\n",
    "\n",
] + lines[end:]
wt.write_text("".join(new_lines), encoding="utf-8")
print("ok", len(body.splitlines()), "lines extracted")
