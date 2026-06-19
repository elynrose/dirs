"""Re-apply structural hygiene patches to worker_tasks.py after git restore."""
from pathlib import Path
import re

TASKS = Path(__file__).resolve().parents[1] / "director_api" / "tasks"
wt_path = TASKS / "worker_tasks.py"
text = wt_path.read_text(encoding="utf-8")

# 1) worker_helpers + pipeline_fallback imports
if "from director_api.tasks.worker_helpers import" not in text:
    text = text.replace(
        "from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export\n",
        """from director_api.tasks.phase5_impl import _phase5_auto_heal_before_export
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
from director_api.services import pipeline_fallback_events as pipeline_fallback_svc
""",
    )

# 2) Remove worker_helpers function bodies (between constants and configure_logging)
text = re.sub(
    r"\ndef _worker_runtime_for_job\(db, job: Job\) -> Settings:.*?^configure_logging\(\)",
    "\n\nconfigure_logging()",
    text,
    count=1,
    flags=re.MULTILINE | re.DOTALL,
)

# 3) Remove stop-signal block through asset guard
text = re.sub(
    r"\ndef _payload_stop_requested\(payload: Any\) -> bool:.*?\ndef _merge_framing_safety_negative",
    "\n\n# Stop-signal helpers and asset-running guard: worker_helpers.py\n\n\ndef _merge_framing_safety_negative",
    text,
    count=1,
    flags=re.MULTILINE | re.DOTALL,
)

# 4) Remove _synthetic_job
text = re.sub(
    r"\ndef _synthetic_job\(\n    \*,\n    tenant_id: str,.*?\n    \)\n\n\ndef _scene_has_succeeded_image",
    "\n\ndef _scene_has_succeeded_image",
    text,
    count=1,
    flags=re.MULTILINE | re.DOTALL,
)

# 5) Remove _record_usage before _phase3_scenes_plan
text = re.sub(
    r"\ndef _record_usage\(\n    db,.*?\n    \)\n\n\ndef _phase3_scenes_plan_for_chapter",
    "\n\ndef _phase3_scenes_plan_for_chapter",
    text,
    count=1,
    flags=re.MULTILINE | re.DOTALL,
)

# 6) phase3 dedup - find and replace phase3 block with import
lines = text.splitlines(keepends=True)
start = next(i for i, l in enumerate(lines) if l.startswith("def _phase3_scenes_plan_for_chapter"))
end = next(i for i, l in enumerate(lines) if l.startswith("def _agent_run_repair_failing_scenes"))
import_block = """from director_api.tasks.phase3_impl import (
    _phase3_image_generate,
    _phase3_scene_extend,
    _phase3_scene_still_job_succeeded,
    _phase3_scenes_generate,
    _phase3_scenes_plan_for_chapter,
    _phase3_video_generate,
)

"""
lines = lines[:start] + [import_block] + lines[end:]
text = "".join(lines)

# 7) partial_failed extra
old_partial = """                    note=(
                        "Some scenes still lack enough succeeded video assets after retries; continuing to timeline and export. "
                        "Re-generate failed clips in Studio, or set agent_run_abort_on_auto_video_failure (or pipeline_options.abort_on_auto_video_failure) to stop the run on this condition."
                    ),
                )"""
new_partial = """                    note=(
                        "Some scenes still lack enough succeeded video assets after retries; continuing to timeline and export. "
                        "Re-generate failed clips in Studio, or set agent_run_abort_on_auto_video_failure (or pipeline_options.abort_on_auto_video_failure) to stop the run on this condition."
                    ),
                    **pipeline_fallback_svc.auto_videos_partial_failed_extra(
                        generated=video_generated,
                        failed_scene_count=len(vid_failed),
                    ),
                )"""
text = text.replace(old_partial, new_partial, 1)

# 8) visual heal event
old_heal = """                heal_out = _phase3_image_generate(db, j_heal)
                if isinstance(heal_out, dict) and heal_out.get("ok") is True:
                    _auto_pipeline_approve_scene_image(db, sc)
                db.commit()"""
new_heal = """                heal_out = _phase3_image_generate(db, j_heal)
                if isinstance(heal_out, dict) and heal_out.get("ok") is True:
                    _auto_pipeline_approve_scene_image(db, sc)
                    run_heal = db.get(AgentRun, agent_run_uuid)
                    if run_heal is not None:
                        _append_event(
                            run_heal,
                            "auto_timeline",
                            "visual_heal",
                            **pipeline_fallback_svc.visual_heal_event_fields(
                                scene_id=str(sc.id),
                                auto_generate_scene_images=auto_scene_images_pre,
                                auto_generate_scene_videos=auto_scene_videos_pre,
                            ),
                        )
                db.commit()"""
text = text.replace(old_heal, new_heal, 1)

# 9) scene_skipped heal_attempted
text = text.replace(
    'reason="no_visual_media",\n                            auto_generate_scene_images=auto_scene_images_pre,',
    'reason="no_visual_media",\n                            heal_attempted=True,\n                            auto_generate_scene_images=auto_scene_images_pre,',
    1,
)

# 10) Celery delegations
text = re.sub(
    r"@celery_app\.task\(name=\"director\.run_adapter_smoke\"\)\ndef run_adapter_smoke_task\(job_id: str\) -> None:.*?(?=\n@celery_app\.task\(name=\"director\.run_phase2_job\")",
    """@celery_app.task(name="director.run_adapter_smoke")
def run_adapter_smoke_task(job_id: str) -> None:
    from director_api.tasks.worker_runtime import run_adapter_smoke_impl as _run_adapter_smoke_impl

    _run_adapter_smoke_impl(job_id)


""",
    text,
    count=1,
    flags=re.MULTILINE | re.DOTALL,
)

text = re.sub(
    r"@celery_app\.task\(name=\"director\.run_phase2_job\".*?\n            notify_job_terminal\(jid, settings\)\n\n\ndef _agent_run_mark_failed",
    """@celery_app.task(name="director.run_phase2_job", soft_time_limit=600, time_limit=720)
def run_phase2_job(job_id: str) -> None:
    from director_api.tasks.worker_runtime import _run_phase2_job_impl

    _run_phase2_job_impl(job_id)


def _agent_run_mark_failed""",
    text,
    count=1,
    flags=re.MULTILINE | re.DOTALL,
)

text = re.sub(
    r"@celery_app\.task\(bind=True, name=\"director\.run_phase4_job\".*?\n            notify_job_terminal\(jid, settings\)\n\n\n@celery_app\.task\(\n    bind=True,\n    name=\"director\.run_phase5_job\"",
    """@celery_app.task(bind=True, name="director.run_phase4_job", soft_time_limit=600, time_limit=720)
def run_phase4_job(self, job_id: str) -> None:
    from director_api.tasks.worker_runtime import _run_phase4_job_impl

    _run_phase4_job_impl(self, job_id)


@celery_app.task(
    bind=True,
    name="director.run_phase5_job""",
    text,
    count=1,
    flags=re.MULTILINE | re.DOTALL,
)

text = re.sub(
    r"@celery_app\.task\(\n    bind=True,\n    name=\"director\.run_phase5_job\".*?\n            notify_job_terminal\(jid, settings\)\n\n\n# reap_stale_jobs",
    """@celery_app.task(
    bind=True,
    name="director.run_phase5_job",
    soft_time_limit=_CELERY_PHASE5_SOFT_SEC,
    time_limit=_CELERY_PHASE5_HARD_SEC,
)
def run_phase5_job(self, job_id: str) -> None:
    from director_api.tasks.worker_runtime import _run_phase5_job_impl

    _run_phase5_job_impl(self, job_id)


# reap_stale_jobs""",
    text,
    count=1,
    flags=re.MULTILINE | re.DOTALL,
)

wt_path.write_text(text, encoding="utf-8")
print("reapplied patches")
