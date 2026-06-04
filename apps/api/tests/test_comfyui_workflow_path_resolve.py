"""ComfyUI workflow path resolution under LOCAL_STORAGE_ROOT."""

from __future__ import annotations

from pathlib import Path

from director_api.config import get_settings
from director_api.providers.media_comfyui import _resolve_path_under_storage_or_repo


def test_resolve_path_under_storage_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    key = "comfyui_workflows/tenant_a/image_workflow.json"
    dest = tmp_path / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("{}", encoding="utf-8")
    resolved = _resolve_path_under_storage_or_repo(key, label="workflow")
    assert resolved == dest.resolve()
