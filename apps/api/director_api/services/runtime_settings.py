from __future__ import annotations

import time
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import AppSetting
from director_api.services.scene_timeline_duration import DEFAULT_SCENE_VO_TAIL_PADDING_SEC

log = structlog.get_logger(__name__)

_RUNTIME_SETTINGS_CACHE: dict[str, tuple[float, Settings]] = {}


def invalidate_runtime_settings_cache(tenant_id: str | None = None) -> None:
    """Drop cached merged Settings for one tenant or all (call after PATCH /v1/settings)."""
    if tenant_id is None:
        _RUNTIME_SETTINGS_CACHE.clear()
        return
    t = tenant_id.strip()
    if t:
        _RUNTIME_SETTINGS_CACHE.pop(t, None)

# DB/infra bootstrap values remain env-backed; runtime behavior can be overridden.
# Job caps must not come from app_settings JSON: PATCH /v1/settings sends the whole client config dict;
# a saved toggle + defaults once persisted `job_caps_enforced` and low caps and overrode .env forever.
_NON_OVERRIDABLE_KEYS = frozenset(
    {
        "database_url",
        "redis_url",
        "local_storage_root",
        "job_caps_enforced",
        "job_cap_media",
        "job_cap_compile",
        "job_cap_text",
        "job_cap_media_global",
    }
)


def _allowed_keys() -> set[str]:
    return set(Settings.model_fields.keys()) - set(_NON_OVERRIDABLE_KEYS)


def get_or_create_app_settings(db: Session, tenant_id: str) -> AppSetting:
    row = db.query(AppSetting).filter(AppSetting.tenant_id == tenant_id).one_or_none()
    if row:
        return row
    row = AppSetting(tenant_id=tenant_id, config_json={})
    db.add(row)
    db.flush()
    return row


def sanitize_overrides(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    allowed = _allowed_keys()
    clean: dict[str, Any] = {}
    for k, v in raw.items():
        if k in allowed and v is not None:
            clean[k] = v
    if "scene_clip_duration_sec" in clean:
        try:
            iv = int(clean["scene_clip_duration_sec"])
            clean["scene_clip_duration_sec"] = 5 if iv == 5 else 10
        except (TypeError, ValueError):
            clean.pop("scene_clip_duration_sec", None)
    if "narration_style_preset" in clean:
        from director_api.style_presets import DEFAULT_NARRATION_PRESET, is_valid_narration_preset

        s = str(clean["narration_style_preset"]).strip()
        clean["narration_style_preset"] = s if is_valid_narration_preset(s) else DEFAULT_NARRATION_PRESET
    if "default_narration_style_ref" in clean:
        from director_api.style_presets import sanitize_default_narration_style_ref

        sn = sanitize_default_narration_style_ref(clean.get("default_narration_style_ref"))
        if sn:
            clean["default_narration_style_ref"] = sn
        else:
            clean.pop("default_narration_style_ref", None)
    if "visual_style_preset" in clean:
        from director_api.style_presets import DEFAULT_VISUAL_PRESET, is_valid_visual_preset

        s = str(clean["visual_style_preset"]).strip()
        clean["visual_style_preset"] = s if is_valid_visual_preset(s) else DEFAULT_VISUAL_PRESET
    if "openai_local_chat_max_tokens" in clean:
        try:
            iv = int(clean["openai_local_chat_max_tokens"])
            clean["openai_local_chat_max_tokens"] = max(512, min(200_000, iv))
        except (TypeError, ValueError):
            clean.pop("openai_local_chat_max_tokens", None)
    if "openai_compatible_text_source" in clean:
        v = str(clean["openai_compatible_text_source"] or "").strip().lower()
        clean["openai_compatible_text_source"] = "lm_studio" if v == "lm_studio" else "openai"
    if "active_text_provider" in clean:
        v = str(clean["active_text_provider"] or "").strip().lower()
        if v == "google":
            v = "gemini"
        if v not in ("openai", "lm_studio", "openrouter", "xai", "grok", "gemini", "default", "auto", ""):
            v = "openai"
        clean["active_text_provider"] = "openai" if v in ("", "default", "auto") else v
    if "comfyui_timeout_sec" in clean:
        try:
            fv = float(clean["comfyui_timeout_sec"])
            clean["comfyui_timeout_sec"] = max(30.0, min(7200.0, fv))
        except (TypeError, ValueError):
            clean.pop("comfyui_timeout_sec", None)
    if "comfyui_video_timeout_sec" in clean:
        try:
            fv = float(clean["comfyui_video_timeout_sec"])
            clean["comfyui_video_timeout_sec"] = max(60.0, min(7200.0, fv))
        except (TypeError, ValueError):
            clean.pop("comfyui_video_timeout_sec", None)
    if "scene_vo_tail_padding_sec" in clean:
        try:
            fv = float(clean["scene_vo_tail_padding_sec"])
            clean["scene_vo_tail_padding_sec"] = max(0.0, min(120.0, fv))
        except (TypeError, ValueError):
            clean.pop("scene_vo_tail_padding_sec", None)
    if "comfyui_poll_interval_sec" in clean:
        try:
            fv = float(clean["comfyui_poll_interval_sec"])
            clean["comfyui_poll_interval_sec"] = max(0.2, min(10.0, fv))
        except (TypeError, ValueError):
            clean.pop("comfyui_poll_interval_sec", None)
    if "agent_run_chapter_critique_max_rounds" in clean:
        try:
            iv = int(clean["agent_run_chapter_critique_max_rounds"])
            clean["agent_run_chapter_critique_max_rounds"] = max(1, min(20, iv))
        except (TypeError, ValueError):
            clean.pop("agent_run_chapter_critique_max_rounds", None)
    for _rk, _lo, _hi in (
        ("agent_run_scene_repair_max_rounds", 0, 8),
        ("agent_run_chapter_repair_max_rounds", 0, 5),
    ):
        if _rk in clean:
            try:
                iv = int(clean[_rk])
                clean[_rk] = max(_lo, min(_hi, iv))
            except (TypeError, ValueError):
                clean.pop(_rk, None)
    if "agent_run_auto_generate_scene_videos" in clean:
        clean["agent_run_auto_generate_scene_videos"] = bool(clean["agent_run_auto_generate_scene_videos"])
    if "comfyui_video_use_scene_image" in clean:
        clean["comfyui_video_use_scene_image"] = bool(clean["comfyui_video_use_scene_image"])
    if "comfyui_api_flavor" in clean:
        fv = str(clean["comfyui_api_flavor"]).strip().lower()
        clean["comfyui_api_flavor"] = "cloud" if fv in ("cloud", "comfy_cloud", "comfy-cloud") else "oss"

    if "active_image_provider" in clean:
        v = str(clean["active_image_provider"] or "").strip().lower()
        if v not in ("fal", "comfyui", "comfy", "placeholder"):
            clean["active_image_provider"] = "fal"
    if "active_video_provider" in clean:
        v = str(clean["active_video_provider"] or "").strip().lower()
        if v not in ("fal", "comfyui_wan", "local_ffmpeg"):
            clean["active_video_provider"] = "fal"

    if "export_chapter_title_card_sec" in clean:
        try:
            fv = float(clean["export_chapter_title_card_sec"])
            clean["export_chapter_title_card_sec"] = max(0.0, min(30.0, fv))
        except (TypeError, ValueError):
            clean.pop("export_chapter_title_card_sec", None)
    if "scene_plan_target_scenes_per_chapter" in clean:
        try:
            iv = int(clean["scene_plan_target_scenes_per_chapter"])
            clean["scene_plan_target_scenes_per_chapter"] = max(0, min(48, iv))
        except (TypeError, ValueError):
            clean.pop("scene_plan_target_scenes_per_chapter", None)

    for _ck in (
        "critic_pass_threshold",
        "chapter_min_scene_pass_ratio",
        "chapter_pass_score_threshold",
        "critic_missing_dimension_default",
    ):
        if _ck in clean:
            try:
                fv = float(clean[_ck])
                clean[_ck] = max(0.0, min(1.0, fv))
            except (TypeError, ValueError):
                clean.pop(_ck, None)
    # Legacy tenant override (merged into critic_missing_dimension_default behavior); strip so Settings model_copy accepts JSON.
    clean.pop("critic_dimension_invalid_fallback", None)
    if "studio_batch_image_interval_sec" in clean:
        try:
            iv = int(clean["studio_batch_image_interval_sec"])
            clean["studio_batch_image_interval_sec"] = max(2, min(3600, iv))
        except (TypeError, ValueError):
            clean.pop("studio_batch_image_interval_sec", None)
    if "studio_job_poll_interval_ms" in clean:
        try:
            iv = int(clean["studio_job_poll_interval_ms"])
            clean["studio_job_poll_interval_ms"] = max(500, min(120_000, iv))
        except (TypeError, ValueError):
            clean.pop("studio_job_poll_interval_ms", None)
    if "kokoro_speed" in clean:
        try:
            fv = float(clean["kokoro_speed"])
            clean["kokoro_speed"] = max(0.25, min(2.5, fv))
        except (TypeError, ValueError):
            clean.pop("kokoro_speed", None)
    if "kokoro_lang_code" in clean:
        lc = str(clean["kokoro_lang_code"] or "a").strip().lower()
        aliases = {"en-us": "a", "en-gb": "b", "english": "a"}
        lc = aliases.get(lc, lc)
        allowed_lang = frozenset("abefhijpz")
        clean["kokoro_lang_code"] = lc if lc in allowed_lang else "a"
    if "kokoro_voice" in clean and clean["kokoro_voice"] is not None:
        v = str(clean["kokoro_voice"]).strip()[:80]
        clean["kokoro_voice"] = v or "af_bella"

    if "visual_preset_overrides" in clean:
        from director_api.style_presets import sanitize_visual_preset_overrides

        clean["visual_preset_overrides"] = sanitize_visual_preset_overrides(clean.get("visual_preset_overrides"))

    # Empty string in DB must not override env: users often clear password inputs and Save, which would wipe FAL_KEY etc.
    _optional_secret_keys = frozenset(
        {
            "fal_key",
            "openai_api_key",
            "lm_studio_api_key",
            "openrouter_api_key",
            "xai_api_key",
            "grok_api_key",
            "tavily_api_key",
            "gemini_api_key",
            "elevenlabs_api_key",
            "webhook_signing_secret",
            "telegram_bot_token",
            "telegram_webhook_secret",
        }
    )
    for sk in _optional_secret_keys:
        if sk in clean and isinstance(clean[sk], str) and not clean[sk].strip():
            clean.pop(sk, None)
    if "telegram_chat_id" in clean:
        tc = str(clean["telegram_chat_id"]).strip()
        if tc:
            clean["telegram_chat_id"] = tc
        else:
            clean.pop("telegram_chat_id", None)
    return clean


def resolve_runtime_settings(db: Session, base: Settings, tenant_id: str | None = None) -> Settings:
    """Merge per-tenant ``app_settings`` overrides into env-backed ``Settings``.

    ``tenant_id`` selects which ``AppSetting`` row to load. When omitted, uses
    ``base.default_tenant_id`` (single-tenant / tests). The returned ``Settings``
    has ``default_tenant_id`` set to the active tenant so existing code paths
    that filter on ``settings.default_tenant_id`` stay correct.

    Results are cached briefly (``runtime_settings_cache_ttl_sec`` on ``Settings``) to avoid
    repeated merges on hot paths; invalidate via ``invalidate_runtime_settings_cache``.
    """
    tid = (tenant_id or base.default_tenant_id or "").strip() or base.default_tenant_id
    ttl = float(getattr(base, "runtime_settings_cache_ttl_sec", 15.0) or 0.0)
    now = time.monotonic()
    if ttl > 0.0:
        hit = _RUNTIME_SETTINGS_CACHE.get(tid)
        if hit is not None and (now - hit[0]) < ttl:
            return hit[1]
    row = db.query(AppSetting).filter(AppSetting.tenant_id == tid).one_or_none()
    overrides = sanitize_overrides(row.config_json if row else {})
    merged: Settings
    if not overrides:
        merged = base
    else:
        try:
            merged = base.model_copy(update=overrides)
        except (ValidationError, TypeError, ValueError) as e:
            log.warning(
                "runtime_settings_invalid_falling_back",
                tenant_id=tid,
                error=str(e),
            )
            merged = base
    if merged.default_tenant_id != tid:
        merged = merged.model_copy(update={"default_tenant_id": tid})
    if ttl > 0.0:
        _RUNTIME_SETTINGS_CACHE[tid] = (now, merged)
    return merged


def scene_vo_tail_padding_sec_for_tenant(db: Session, tenant_id: str) -> float:
    """Tail padding for timeline helpers that have DB + tenant but no injected request Settings."""
    from director_api.config import get_settings

    merged = resolve_runtime_settings(db, get_settings(), tenant_id)
    d = DEFAULT_SCENE_VO_TAIL_PADDING_SEC
    v = float(getattr(merged, "scene_vo_tail_padding_sec", d) or d)
    return max(0.0, min(120.0, v))
