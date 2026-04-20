"""Unit tests for ComfyUI provider helpers (no live ComfyUI required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from director_api.providers import media_comfyui as mc


def test_http_base_to_ws_base() -> None:
    assert mc._http_base_to_ws_base("http://127.0.0.1:8188") == "ws://127.0.0.1:8188"
    assert mc._http_base_to_ws_base("https://example.com/foo") == "wss://example.com/foo"


def test_validate_workflow_nodes_prompt_node_missing() -> None:
    wf: dict = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "9": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
    }
    errs, _ = mc._validate_workflow_nodes_in_graph(
        wf,
        prompt_node_id="99",
        prompt_field="text",
        negative_node_id="",
        load_image_node_id="",
        require_load_image=False,
    )
    assert any(e.startswith("prompt_node_missing") for e in errs)


def test_validate_workflow_nodes_load_image_required() -> None:
    wf: dict = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
    }
    errs, _ = mc._validate_workflow_nodes_in_graph(
        wf,
        prompt_node_id="",
        prompt_field="text",
        negative_node_id="",
        load_image_node_id="",
        require_load_image=True,
    )
    assert "load_image_node_id_required_when_comfyui_video_use_scene_image" in errs


def test_workflow_env_report_from_path(tmp_path: Path) -> None:
    p = tmp_path / "wf.json"
    p.write_text(
        '{"3": {"class_type": "CLIPTextEncode", "inputs": {"text": "p"}}, '
        '"4": {"class_type": "CLIPTextEncode", "inputs": {"text": "n"}}, '
        '"5": {"class_type": "LoadImage", "inputs": {"image": "in.png"}}}',
        encoding="utf-8",
    )
    rep = mc._workflow_env_report_from_path(
        "video",
        p,
        prompt_node_id="",
        prompt_field="text",
        negative_node_id="",
        load_image_node_id="5",
        require_load_image=True,
    )
    assert rep["ok"] is True
    assert rep["errors"] == []


@pytest.mark.parametrize(
    ("base", "client_id", "prompt_id"),
    [
        ("http://127.0.0.1:8188", "cid-test", "pid-test"),
    ],
)
def test_spawn_ws_watcher_returns_event_or_none(
    base: str, client_id: str, prompt_id: str
) -> None:
    """WS thread may fail without a server; we only require a handle or graceful None."""
    ev = mc._spawn_comfyui_ws_done_watcher(
        base, client_id, prompt_id, deadline=__import__("time").monotonic() + 0.5
    )
    assert ev is None or hasattr(ev, "is_set")
