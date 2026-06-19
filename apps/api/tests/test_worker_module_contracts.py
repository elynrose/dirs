"""Cross-module signature contracts — catch caller/callee drift before runtime TypeError."""

from __future__ import annotations

import inspect

import pytest

from director_api.providers.media_comfyui import generate_scene_image_comfyui, generate_scene_video_comfyui
from director_api.services.narration_bracket_visual import video_text_prompt_from_scene_fields
from director_api.tasks import worker_tasks
from director_api.tasks import phase3_impl


def test_worker_tasks_imports_cleanly():
    assert worker_tasks.run_agent_run is not None
    assert worker_tasks.run_phase3_job is not None


def test_video_text_prompt_from_scene_fields_accepts_video_prompt_suffix():
    sig = inspect.signature(video_text_prompt_from_scene_fields)
    assert "video_prompt_suffix" in sig.parameters


def test_generate_scene_video_comfyui_accepts_should_stop():
    sig = inspect.signature(generate_scene_video_comfyui)
    assert "should_stop" in sig.parameters


def test_generate_scene_image_comfyui_accepts_should_stop():
    sig = inspect.signature(generate_scene_image_comfyui)
    assert "should_stop" in sig.parameters


def test_worker_tasks_resolve_phase3_video_prompt_forwards_suffix():
    sig = inspect.signature(worker_tasks._resolve_phase3_video_text_prompt)
    assert "suffix" in sig.parameters
    src = inspect.getsource(worker_tasks._resolve_phase3_video_text_prompt)
    assert "video_prompt_suffix" in src


def test_phase3_impl_resolve_phase3_video_prompt_forwards_suffix():
    sig = inspect.signature(phase3_impl._resolve_phase3_video_text_prompt)
    assert "suffix" in sig.parameters
    src = inspect.getsource(phase3_impl._resolve_phase3_video_text_prompt)
    assert "video_prompt_suffix" in src


def test_phase3_video_generate_calls_comfyui_with_should_stop():
    src = inspect.getsource(phase3_impl._phase3_video_generate)
    assert "generate_scene_video_comfyui" in src
    assert "should_stop" in src


def test_phase3_impl_binds_phase3_llm():
    from director_api.agents import phase3_llm

    assert phase3_impl.phase3_llm is phase3_llm


def test_phase5_compile_impl_binds_agent_run_control():
    from director_api.tasks import phase5_compile_impl as p5
    from director_api.tasks.agent_run_control import agent_run_checkpoint, payload_agent_run_uuid

    assert p5._payload_agent_run_uuid is payload_agent_run_uuid
    assert p5._agent_run_checkpoint is agent_run_checkpoint


def test_worker_tasks_phase3_image_generate_is_canonical_or_imported():
    """After dedup (A2), worker_tasks must re-export phase3_impl."""
    from director_api.tasks import phase3_impl

    assert worker_tasks._phase3_image_generate is phase3_impl._phase3_image_generate
    assert worker_tasks._phase3_video_generate is phase3_impl._phase3_video_generate


@pytest.mark.parametrize(
    "module,attr",
    [
        (worker_tasks, "_synthetic_job"),
        (worker_tasks, "_record_usage"),
        (worker_tasks, "_worker_runtime_for_job"),
    ],
)
def test_worker_tasks_exports_helper(module, attr):
    assert hasattr(module, attr)
    assert callable(getattr(module, attr))


def test_worker_helpers_imports():
    from director_api.tasks import worker_helpers as wh

    assert callable(wh.synthetic_job)
    assert callable(wh.record_usage)
    assert callable(wh.make_job_stop_signal)
    assert wh._synthetic_job is wh.synthetic_job
