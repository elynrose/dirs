from __future__ import annotations

import time
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import AppSetting, TenantMembership, User
from director_api.services.scene_timeline_duration import DEFAULT_SCENE_VO_TAIL_PADDING_SEC
from director_api.services.tenant_entitlements import (
    ENTITLEMENT_PLATFORM_API_CREDENTIALS,
    get_effective_entitlements,
)

log = structlog.get_logger(__name__)

_RUNTIME_SETTINGS_CACHE: dict[str, tuple[float, Settings]] = {}


def invalidate_runtime_settings_cache(tenant_id: str | None = None) -> None:
    """Drop cached merged Settings for one tenant or all (call after PATCH /v1/settings)."""
    if tenant_id is None:
        _RUNTIME_SETTINGS_CACHE.clear()
        return
    t = tenant_id.strip()
    if not t:
        return
    for k in list(_RUNTIME_SETTINGS_CACHE.keys()):
        if k == t or k.startswith(t + "\x1f"):
            _RUNTIME_SETTINGS_CACHE.pop(k, None)


def invalidate_runtime_settings_cache_for_user(db: Session, user_id: int) -> None:
    """Drop cache rows for every workspace this user belongs to (membership or credential flag changes)."""
    rows = db.scalars(select(TenantMembership.tenant_id).where(TenantMembership.user_id == user_id)).all()
    for tid in {str(x) for x in rows if x}:
        invalidate_runtime_settings_cache(tenant_id=tid)


def invalidate_runtime_settings_cache_after_tenant_config_persisted(
    base: Settings, written_tenant_id: str
) -> None:
    """Invalidate merged runtime settings after ``AppSetting.config_json`` is saved.

    Child workspaces merge optional API keys from ``director_platform_credentials_source_tenant_id``.
    When that *source* row changes, every tenant's cached merge can be stale, so we clear the whole cache.
    """
    tid = (written_tenant_id or "").strip()
    if tid:
        invalidate_runtime_settings_cache(tenant_id=tid)
    src = (getattr(base, "director_platform_credentials_source_tenant_id", None) or "").strip()
    if src and tid == src:
        invalidate_runtime_settings_cache()


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
    if "agent_run_auto_generate_scene_images" in clean:
        clean["agent_run_auto_generate_scene_images"] = bool(clean["agent_run_auto_generate_scene_images"])
    for _mk in ("agent_run_min_scene_images", "agent_run_min_scene_videos"):
        if _mk in clean:
            try:
                mv = int(clean[_mk])
                clean[_mk] = max(1, min(10, mv))
            except (TypeError, ValueError):
                clean.pop(_mk, None)
    if "agent_run_auto_images_max_concurrency" in clean:
        try:
            mc = int(clean["agent_run_auto_images_max_concurrency"])
            clean["agent_run_auto_images_max_concurrency"] = max(1, min(8, mc))
        except (TypeError, ValueError):
            clean.pop("agent_run_auto_images_max_concurrency", None)
    if "agent_run_abort_on_auto_video_failure" in clean:
        clean["agent_run_abort_on_auto_video_failure"] = bool(clean["agent_run_abort_on_auto_video_failure"])
    if "agent_run_pipeline_speed" in clean:
        v = str(clean["agent_run_pipeline_speed"]).strip().lower()
        clean["agent_run_pipeline_speed"] = v if v in ("demo_fast", "production_heavy") else "standard"
    if "studio_default_mix_music_volume" in clean:
        try:
            fv = float(clean["studio_default_mix_music_volume"])
            clean["studio_default_mix_music_volume"] = max(0.0, min(1.0, fv))
        except (TypeError, ValueError):
            clean.pop("studio_default_mix_music_volume", None)
    if "studio_default_mix_narration_volume" in clean:
        try:
            fv = float(clean["studio_default_mix_narration_volume"])
            clean["studio_default_mix_narration_volume"] = max(0.0, min(4.0, fv))
        except (TypeError, ValueError):
            clean.pop("studio_default_mix_narration_volume", None)
    if "comfyui_video_use_scene_image" in clean:
        clean["comfyui_video_use_scene_image"] = bool(clean["comfyui_video_use_scene_image"])
    if "comfyui_use_websocket" in clean:
        clean["comfyui_use_websocket"] = bool(clean["comfyui_use_websocket"])
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
            "comfyui_api_key",
            "webhook_signing_secret",
            "telegram_bot_token",
            "telegram_webhook_secret",
            "youtube_client_secret",
            "youtube_refresh_token",
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
    if "telegram_notify_pipeline_failures" in clean:
        clean["telegram_notify_pipeline_failures"] = bool(clean["telegram_notify_pipeline_failures"])
    if "youtube_auto_upload_after_export" in clean:
        clean["youtube_auto_upload_after_export"] = bool(clean["youtube_auto_upload_after_export"])
    if "youtube_share_watch_link_in_telegram" in clean:
        clean["youtube_share_watch_link_in_telegram"] = bool(clean["youtube_share_watch_link_in_telegram"])
    if "burn_subtitles_in_final_cut_default" in clean:
        clean["burn_subtitles_in_final_cut_default"] = bool(clean["burn_subtitles_in_final_cut_default"])
    if "youtube_default_privacy" in clean:
        pv = str(clean["youtube_default_privacy"] or "").strip().lower()
        if pv not in ("public", "unlisted", "private"):
            pv = "unlisted"
        clean["youtube_default_privacy"] = pv
    for _url_key in ("director_public_app_url", "public_api_base_url", "youtube_client_id"):
        if _url_key in clean and isinstance(clean[_url_key], str):
            v = clean[_url_key].strip()
            clean[_url_key] = v or None
    return clean


# Keys that may be inherited from ``Settings.director_platform_credentials_source_tenant_id`` (same set as optional secrets in ``sanitize_overrides``).
PLATFORM_CREDENTIAL_SETTING_KEYS = frozenset(
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
        "comfyui_api_key",
        "webhook_signing_secret",
        "telegram_bot_token",
        "telegram_webhook_secret",
        "youtube_client_secret",
        "youtube_refresh_token",
    }
)


def _platform_credentials_source_configured(base: Settings, tenant_id: str) -> bool:
    src_tid = (getattr(base, "director_platform_credentials_source_tenant_id", None) or "").strip()
    return bool(src_tid and src_tid != (tenant_id or "").strip())


def _user_flag_allows_platform_credentials(db: Session, tenant_id: str, explicit_user_id: int | None) -> bool:
    """Per-user admin flag, or exactly one flagged member when ``explicit_user_id`` is None (worker)."""
    tid = (tenant_id or "").strip()
    if not tid:
        return False
    if explicit_user_id is not None:
        u = db.get(User, explicit_user_id)
        return bool(u and getattr(u, "use_platform_api_credentials", False))
    rows = list(
        db.scalars(
            select(User.id)
            .join(TenantMembership, TenantMembership.user_id == User.id)
            .where(
                TenantMembership.tenant_id == tid,
                User.use_platform_api_credentials.is_(True),
            )
        ).all()
    )
    return len(rows) == 1


def tenant_may_inherit_platform_api_credentials(
    db: Session, base: Settings, tenant_id: str, explicit_user_id: int | None
) -> bool:
    """Whether optional API keys may be merged from the platform source tenant for this request."""
    tid = (tenant_id or "").strip()
    if not tid or not _platform_credentials_source_configured(base, tid):
        return False
    if not bool(getattr(base, "director_auth_enabled", False)):
        # Legacy / self-hosted (auth off): if a platform source workspace is configured for *other*
        # tenants, always allow merge — including Celery workers with ``user_id=None``. Requiring exactly
        # one ``User.use_platform_api_credentials`` flag was brittle and caused intermittent "missing
        # credentials" when jobs ran without that narrow condition.
        return True
    ent = get_effective_entitlements(db, tid, auth_enabled=True)
    if bool(ent.get(ENTITLEMENT_PLATFORM_API_CREDENTIALS, False)):
        return True
    return _user_flag_allows_platform_credentials(db, tid, explicit_user_id)


def _merge_platform_credentials(
    db: Session,
    base: Settings,
    merged: Settings,
    tenant_id: str,
    apply_platform_merge: bool,
) -> Settings:
    if not apply_platform_merge:
        return merged
    src_tid = (getattr(base, "director_platform_credentials_source_tenant_id", None) or "").strip()
    if not src_tid or src_tid == tenant_id:
        return merged
    src_row = db.query(AppSetting).filter(AppSetting.tenant_id == src_tid).one_or_none()
    src_ov = sanitize_overrides(src_row.config_json if src_row else {})
    if not src_ov:
        return merged
    updates: dict[str, Any] = {}
    for key in PLATFORM_CREDENTIAL_SETTING_KEYS:
        cur = getattr(merged, key, None)
        cur_s = str(cur).strip() if cur is not None else ""
        if cur_s:
            continue
        if key not in src_ov:
            continue
        val = src_ov[key]
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        updates[key] = val
    if not updates:
        return merged
    try:
        return merged.model_copy(update=updates)
    except (ValidationError, TypeError, ValueError) as e:
        log.warning(
            "platform_credentials_merge_failed",
            tenant_id=tenant_id,
            source_tenant_id=src_tid,
            error=str(e),
        )
        return merged


def platform_inherited_credential_keys_for_settings_response(
    db: Session,
    *,
    tenant_id: str,
    user_id: int | None,
    saved_config: dict[str, Any],
    base_settings: Settings,
) -> list[str]:
    """Secret keys supplied via platform tenant but not stored on this workspace (Studio hides inputs)."""
    if not tenant_may_inherit_platform_api_credentials(db, base_settings, tenant_id, user_id):
        return []
    src_tid = (getattr(base_settings, "director_platform_credentials_source_tenant_id", None) or "").strip()
    if not src_tid or src_tid == tenant_id:
        return []
    src_row = db.query(AppSetting).filter(AppSetting.tenant_id == src_tid).one_or_none()
    src_clean = sanitize_overrides(src_row.config_json if src_row else {})
    out: list[str] = []
    for key in PLATFORM_CREDENTIAL_SETTING_KEYS:
        sav = saved_config.get(key)
        sav_empty = sav is None or (isinstance(sav, str) and not str(sav).strip())
        if not sav_empty:
            continue
        if key not in src_clean:
            continue
        v = src_clean[key]
        if v is None or (isinstance(v, str) and not str(v).strip()):
            continue
        out.append(key)
    return out


def redact_settings_config_for_api(saved: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bool]]:
    """Strip credential values from a settings dict for JSON responses; presence map for UI labels."""
    presence: dict[str, bool] = {}
    out = dict(saved)
    for sk in PLATFORM_CREDENTIAL_SETTING_KEYS:
        if sk not in out:
            continue
        v = out.pop(sk, None)
        if v is None:
            continue
        if isinstance(v, str) and not str(v).strip():
            continue
        presence[sk] = True
    return out, presence


def merge_app_settings_config_patch(prior: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge client patch into prior DB config. Secret keys: empty string does not wipe prior; ``None`` clears."""
    out = dict(prior)
    if not isinstance(patch, dict):
        return out
    for k, v in patch.items():
        if k in PLATFORM_CREDENTIAL_SETTING_KEYS:
            if v is None:
                out.pop(k, None)
                continue
            if isinstance(v, str) and not str(v).strip():
                prev = prior.get(k)
                prev_nonempty = prev is not None and (
                    not isinstance(prev, str) or bool(str(prev).strip())
                )
                if prev_nonempty:
                    continue
                out.pop(k, None)
                continue
            out[k] = v
            continue
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def resolve_runtime_settings(
    db: Session, base: Settings, tenant_id: str | None = None, user_id: int | None = None
) -> Settings:
    """Merge per-tenant ``app_settings`` overrides into env-backed ``Settings``.

    ``tenant_id`` selects which ``AppSetting`` row to load. When omitted, uses
    ``base.default_tenant_id`` (single-tenant / tests). The returned ``Settings``
    has ``default_tenant_id`` set to the active tenant so existing code paths
    that filter on ``settings.default_tenant_id`` stay correct.

    When the workspace (plan entitlement) or the user has ``use_platform_api_credentials``, optional API keys
    missing on this tenant are filled from ``director_platform_credentials_source_tenant_id`` (env).
    With auth on, plan entitlement ``platform_api_credentials`` allows merge even when ``user_id`` is None.
    With auth off and a source tenant configured, merge is always allowed for non-source workspaces.

    Results are cached briefly (``runtime_settings_cache_ttl_sec`` on ``Settings``) to avoid
    repeated merges on hot paths; invalidate via ``invalidate_runtime_settings_cache``.
    """
    tid = (tenant_id or base.default_tenant_id or "").strip() or base.default_tenant_id
    apply_pc = tenant_may_inherit_platform_api_credentials(db, base, tid, user_id)
    cache_key = f"{tid}\x1f{user_id if user_id is not None else 'none'}\x1f{int(apply_pc)}"
    ttl = float(getattr(base, "runtime_settings_cache_ttl_sec", 15.0) or 0.0)
    now = time.monotonic()
    if ttl > 0.0:
        hit = _RUNTIME_SETTINGS_CACHE.get(cache_key)
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
    merged = _merge_platform_credentials(db, base, merged, tid, apply_pc)
    if merged.default_tenant_id != tid:
        merged = merged.model_copy(update={"default_tenant_id": tid})
    if ttl > 0.0:
        _RUNTIME_SETTINGS_CACHE[cache_key] = (now, merged)
    return merged


def scene_vo_tail_padding_sec_for_tenant(db: Session, tenant_id: str) -> float:
    """Tail padding for timeline helpers that have DB + tenant but no injected request Settings."""
    from director_api.config import get_settings

    merged = resolve_runtime_settings(db, get_settings(), tenant_id)
    d = DEFAULT_SCENE_VO_TAIL_PADDING_SEC
    v = float(getattr(merged, "scene_vo_tail_padding_sec", d) or d)
    return max(0.0, min(120.0, v))
