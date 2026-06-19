"""Thumbnail provider routing uses workspace image generation settings."""

from __future__ import annotations

from unittest.mock import MagicMock

from director_api.services.image_provider_routing import resolve_image_provider


def _settings(**kwargs):
    s = MagicMock()
    s.active_image_provider = kwargs.get("active_image_provider", "comfyui")
    s.fal_smoke_model = "fal-ai/fast-sdxl"
    s.comfyui_workflow_json_path = kwargs.get("comfyui_workflow_json_path", "D:/workflows/image_workflow.json")
    s.comfyui_model_name = kwargs.get("comfyui_model_name", "")
    return s


def _project(**kwargs):
    p = MagicMock()
    p.preferred_image_provider = kwargs.get("preferred_image_provider", "fal")
    return p


def test_thumbnail_prefers_workspace_active_image_provider() -> None:
    p = _project(preferred_image_provider="fal")
    s = _settings(active_image_provider="comfyui")
    resolved = resolve_image_provider(p, s, prefer_workspace_settings=True)
    assert resolved.provider == "comfyui"
    assert resolved.model_name == "image_workflow.json"


def test_scene_jobs_prefer_project_provider() -> None:
    p = _project(preferred_image_provider="comfyui")
    s = _settings(active_image_provider="fal")
    resolved = resolve_image_provider(p, s, prefer_workspace_settings=False)
    assert resolved.provider == "comfyui"
