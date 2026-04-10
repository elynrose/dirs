"""Provider adapters — keep SDK / vendor HTTP inside this package only."""

from typing import Any, Callable

from director_api.config import Settings

from director_api.providers.llm_gemini import smoke_llm as gemini_smoke
from director_api.providers.llm_openai import smoke_llm as openai_smoke
from director_api.providers.llm_openrouter import smoke_llm as openrouter_smoke
from director_api.providers.media_comfyui import smoke_image as comfyui_smoke
from director_api.providers.media_fal import smoke_image, smoke_video

SmokeFn = Callable[[Settings], dict[str, Any]]


def fal_smoke(settings: Settings) -> dict[str, Any]:
    """Runs image + text-to-video probe (see ``video_smoke``; I2V-only default model is skipped)."""
    img = smoke_image(settings)
    v = smoke_video(settings, download=False)
    out: dict[str, Any] = dict(img)
    out["video_smoke"] = v
    return out


SMOKE_BY_PROVIDER: dict[str, SmokeFn] = {
    "openai": openai_smoke,
    # Same OpenAI SDK path as openai; with active_text_provider=lm_studio (or routing) hits LM Studio.
    "lm_studio": openai_smoke,
    "openrouter": openrouter_smoke,
    "fal": fal_smoke,
    "comfyui": comfyui_smoke,
    "comfy": comfyui_smoke,
    "gemini": gemini_smoke,
    "google": gemini_smoke,
}


def run_adapter_smoke(provider: str, settings: Settings) -> dict[str, Any]:
    key = provider.lower().strip()
    fn = SMOKE_BY_PROVIDER.get(key)
    if not fn:
        return {"configured": False, "error": f"unknown provider: {provider}"}
    return fn(settings)
