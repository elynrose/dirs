"""Shared image provider routing for scene assets and publish thumbnails."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from director_api.config import Settings
from director_api.db.models import Project


@dataclass(frozen=True)
class ResolvedImageProvider:
    requested: str
    provider: str  # comfyui | fal | placeholder | none
    model_name: str | None = None


def resolve_image_provider(
    project: Project,
    settings: Settings,
    *,
    override: str | None = None,
    prefer_workspace_settings: bool = False,
) -> ResolvedImageProvider:
    """
    Resolve which image backend to use.

    Scene jobs: project ``preferred_image_provider`` then workspace ``active_image_provider``.
    Publish thumbnails: workspace ``active_image_provider`` first (Settings → Generation).
    """
    if isinstance(override, str) and override.strip():
        requested = override.strip()
    elif prefer_workspace_settings:
        requested = (
            getattr(settings, "active_image_provider", None)
            or project.preferred_image_provider
            or "fal"
        )
    else:
        requested = (
            project.preferred_image_provider
            or getattr(settings, "active_image_provider", None)
            or "fal"
        )
    req_l = str(requested).lower().strip()
    if req_l in ("auto", "default", ""):
        req_l = str(getattr(settings, "active_image_provider", None) or "fal").lower().strip()
    if req_l in ("openai", "grok", "xai", "gemini", "google"):
        req_l = "fal"

    if req_l in ("comfyui", "comfy"):
        wf = (settings.comfyui_workflow_json_path or "").strip()
        model = (settings.comfyui_model_name or "").strip() or (Path(wf).name if wf else "comfyui")
        return ResolvedImageProvider(requested=requested, provider="comfyui", model_name=model)
    if req_l == "placeholder":
        return ResolvedImageProvider(requested=requested, provider="placeholder", model_name="lavfi_color")
    if req_l == "fal":
        model = (settings.fal_smoke_model or "fal-ai/fast-sdxl").strip().lstrip("/")
        return ResolvedImageProvider(requested=requested, provider="fal", model_name=model)
    return ResolvedImageProvider(requested=requested, provider="none", model_name=None)
