"""Workspace ComfyUI API-format workflow JSON files under ``LOCAL_STORAGE_ROOT``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from director_api.services.chatterbox_voice_ref import safe_tenant_slug

ComfyuiWorkflowRole = Literal["image", "video"]

_ROLE_FILENAMES: dict[ComfyuiWorkflowRole, str] = {
    "image": "image_workflow.json",
    "video": "video_workflow.json",
}

_CONFIG_KEY_BY_ROLE: dict[ComfyuiWorkflowRole, str] = {
    "image": "comfyui_workflow_json_path",
    "video": "comfyui_video_workflow_json_path",
}

_TEST_OUTPUT_NAMES: dict[ComfyuiWorkflowRole, str] = {
    "image": "test_output.png",
    "video": "test_output.mp4",
}


def workflow_storage_key(tenant_id: str, role: ComfyuiWorkflowRole) -> str:
    slug = safe_tenant_slug(tenant_id)
    return f"comfyui_workflows/{slug}/{_ROLE_FILENAMES[role]}"


def test_output_storage_key(tenant_id: str, role: ComfyuiWorkflowRole) -> str:
    slug = safe_tenant_slug(tenant_id)
    return f"comfyui_workflows/{slug}/{_TEST_OUTPUT_NAMES[role]}"


def workflow_config_key(role: ComfyuiWorkflowRole) -> str:
    return _CONFIG_KEY_BY_ROLE[role]


def workflow_absolute_path(*, storage_root: Path, tenant_id: str, role: ComfyuiWorkflowRole) -> Path:
    return (storage_root / workflow_storage_key(tenant_id, role)).resolve()


def test_output_absolute_path(*, storage_root: Path, tenant_id: str, role: ComfyuiWorkflowRole) -> Path:
    return (storage_root / test_output_storage_key(tenant_id, role)).resolve()


def tenant_workflow_dir(*, storage_root: Path, tenant_id: str) -> Path:
    slug = safe_tenant_slug(tenant_id)
    return (storage_root / "comfyui_workflows" / slug).resolve()


def parse_comfyui_api_workflow_json(raw: bytes) -> dict[str, Any]:
    if len(raw) < 2:
        raise ValueError("workflow JSON is empty")
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("workflow JSON exceeds 5 MB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"workflow must be UTF-8 JSON: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("ComfyUI API workflow must be a JSON object (node id → node dict)")
    if not data:
        raise ValueError("workflow JSON object is empty")
    return data


def save_workflow_json(
    *,
    storage_root: Path,
    tenant_id: str,
    role: ComfyuiWorkflowRole,
    workflow: dict[str, Any],
) -> str:
    dest = workflow_absolute_path(storage_root=storage_root, tenant_id=tenant_id, role=role)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
    return workflow_storage_key(tenant_id, role)


def workflow_role_info(
    *,
    storage_root: Path,
    tenant_id: str,
    role: ComfyuiWorkflowRole,
    configured_path: str,
) -> dict[str, Any]:
    key = workflow_storage_key(tenant_id, role)
    path = workflow_absolute_path(storage_root=storage_root, tenant_id=tenant_id, role=role)
    has_file = path.is_file()
    node_count: int | None = None
    if has_file:
        try:
            wf = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(wf, dict):
                node_count = len(wf)
        except (OSError, json.JSONDecodeError):
            node_count = None
    return {
        "role": role,
        "has_workflow": has_file,
        "storage_key": key if has_file else None,
        "configured_path": (configured_path or "").strip() or None,
        "resolved_path": str(path) if has_file else None,
        "node_count": node_count,
        "config_key": workflow_config_key(role),
    }


def delete_workflow_file(*, storage_root: Path, tenant_id: str, role: ComfyuiWorkflowRole) -> None:
    path = workflow_absolute_path(storage_root=storage_root, tenant_id=tenant_id, role=role)
    tenant_dir = tenant_workflow_dir(storage_root=storage_root, tenant_id=tenant_id)
    try:
        path.resolve().relative_to(tenant_dir)
    except ValueError:
        return
    path.unlink(missing_ok=True)
