"""Unit tests for ComfyUI workflow JSON storage (no full app import)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from director_api.services.comfyui_workflow_storage import (
    parse_comfyui_api_workflow_json,
    save_workflow_json,
    workflow_role_info,
    workflow_storage_key,
)


def test_parse_valid_workflow() -> None:
    raw = json.dumps({"3": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}}}).encode()
    wf = parse_comfyui_api_workflow_json(raw)
    assert "3" in wf


def test_parse_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_comfyui_api_workflow_json(b"[]")


def test_save_and_role_info(tmp_path: Path) -> None:
    wf = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hi"}}}
    key = save_workflow_json(storage_root=tmp_path, tenant_id="t1", role="image", workflow=wf)
    assert key == workflow_storage_key("t1", "image")
    info = workflow_role_info(
        storage_root=tmp_path,
        tenant_id="t1",
        role="image",
        configured_path=key,
    )
    assert info["has_workflow"] is True
    assert info["node_count"] == 1
