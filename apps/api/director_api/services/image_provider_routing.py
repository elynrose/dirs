"""Shared image provider routing for scene assets and publish thumbnails."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from director_api.config import Settings
from director_api.db.models import Project

# Image providers that generate a still from a text prompt (excludes "placeholder"/"none").
IMAGE_GENERATION_PROVIDERS = ("comfyui", "fal", "openai", "grok", "gemini")


@dataclass(frozen=True)
class ResolvedImageProvider:
    requested: str
    provider: str  # comfyui | fal | openai | grok | gemini | placeholder | none
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
    if req_l == "xai":
        req_l = "grok"
    if req_l == "google":
        req_l = "gemini"

    if req_l in ("comfyui", "comfy"):
        wf = (settings.comfyui_workflow_json_path or "").strip()
        model = (settings.comfyui_model_name or "").strip() or (Path(wf).name if wf else "comfyui")
        return ResolvedImageProvider(requested=requested, provider="comfyui", model_name=model)
    if req_l == "placeholder":
        return ResolvedImageProvider(requested=requested, provider="placeholder", model_name="lavfi_color")
    if req_l == "openai":
        model = (getattr(settings, "openai_image_model", None) or "gpt-image-1").strip()
        return ResolvedImageProvider(requested=requested, provider="openai", model_name=model)
    if req_l == "grok":
        model = (getattr(settings, "grok_image_model", None) or "grok-2-image-1212").strip()
        return ResolvedImageProvider(requested=requested, provider="grok", model_name=model)
    if req_l == "gemini":
        model = (getattr(settings, "gemini_image_model", None) or "imagen-4.0-generate-001").strip()
        return ResolvedImageProvider(requested=requested, provider="gemini", model_name=model)
    if req_l == "fal":
        model = (settings.fal_smoke_model or "fal-ai/fast-sdxl").strip().lstrip("/")
        return ResolvedImageProvider(requested=requested, provider="fal", model_name=model)
    return ResolvedImageProvider(requested=requested, provider="none", model_name=None)


def dispatch_image_generation(
    settings: Settings,
    provider: str,
    prompt: str,
    *,
    negative_prompt: str | None = None,
    frame_aspect_ratio: str | None = None,
    model_path: str | None = None,
) -> dict[str, Any]:
    """Call the right provider's ``generate_scene_image`` and return the standard media dict.

    ``provider`` must be one of :data:`IMAGE_GENERATION_PROVIDERS`. Imports are local so a broken
    optional dependency in one adapter never breaks the others at import time.
    """
    p = (provider or "").lower().strip()
    if p in ("comfyui", "comfy"):
        from director_api.providers.media_comfyui import generate_scene_image_comfyui

        return generate_scene_image_comfyui(
            settings, prompt, negative_prompt=negative_prompt or None, frame_aspect_ratio=frame_aspect_ratio
        )
    if p == "openai":
        from director_api.providers.media_openai import generate_scene_image as _gen

        return _gen(settings, prompt, model_path=model_path, negative_prompt=negative_prompt, frame_aspect_ratio=frame_aspect_ratio)
    if p == "grok":
        from director_api.providers.media_grok import generate_scene_image as _gen

        return _gen(settings, prompt, model_path=model_path, negative_prompt=negative_prompt, frame_aspect_ratio=frame_aspect_ratio)
    if p == "gemini":
        from director_api.providers.media_gemini import generate_scene_image as _gen

        return _gen(settings, prompt, model_path=model_path, negative_prompt=negative_prompt, frame_aspect_ratio=frame_aspect_ratio)
    from director_api.providers.media_fal import generate_scene_image as _gen

    return _gen(settings, prompt, model_path=model_path, negative_prompt=negative_prompt, frame_aspect_ratio=frame_aspect_ratio)
