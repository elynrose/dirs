"""Impl modules must bind symbols they call directly (not via ``_wt()`` lazy load)."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

# Modules extracted from worker_tasks / agent tail — direct calls must resolve on the module.
IMPL_MODULES = [
    "director_api.tasks.phase3_impl",
    "director_api.tasks.phase4_impl",
    "director_api.tasks.phase5_compile_impl",
    "director_api.services.scene_narration_tts",
    "director_api.services.publish_pack",
    "director_api.services.publish_outro",
]

# Names resolved at runtime via ``_wt()`` in phase3/4/agent — not required on module dict.
LAZY_WT_NAMES = frozenset(
    {
        "_wt",
        "_flush_llm_usage",
        "_worker_runtime_for_job",
        "_payload_agent_run_uuid",
        "_agent_run_checkpoint",
        "_record_usage",
        "_bind_asset_local_file",
        "_next_timeline_sequence_for_scene",
        "_make_job_stop_signal",
        "_scene_clip_duration_sec",
        "_project_export_dimensions",
    }
)


def _direct_undefined_names(module_name: str) -> list[tuple[str, str]]:
    mod = importlib.import_module(module_name)
    path = Path(mod.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = set(dir(mod)) | set(__builtins__.keys())
    issues: list[tuple[str, str]] = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        fn = getattr(mod, node.name, None)
        if fn is None:
            continue
        for name in fn.__code__.co_names:
            if name in LAZY_WT_NAMES:
                continue
            if name.startswith("_") and name not in fn.__globals__:
                issues.append((node.name, name))
    return sorted(set(issues))


def test_impl_modules_have_direct_bindings():
    failures: list[str] = []
    for module_name in IMPL_MODULES:
        bad = _direct_undefined_names(module_name)
        if bad:
            failures.append(f"{module_name}: {bad[:8]}")
    assert not failures, "Missing direct bindings:\n" + "\n".join(failures)


def test_phase5_compile_impl_key_symbols():
    from director_api.tasks import phase5_compile_impl as p5
    from director_api.tasks.agent_run_control import agent_run_checkpoint, payload_agent_run_uuid
    from director_api.tasks.worker_helpers import record_usage

    assert p5._payload_agent_run_uuid is payload_agent_run_uuid
    assert p5._agent_run_checkpoint is agent_run_checkpoint
    assert p5._record_usage is record_usage
    assert p5.phase3_svc is not None
    assert p5.sanitize_jsonb_text is not None
    assert p5._project_export_dimensions is not None
