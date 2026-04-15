"""Application settings.

Loading precedence (highest → lowest):
  1. .env file values for JOB_CAP_* keys  (see _DotenvJobCapSource)
  2. OS / process environment variables
  3. .env file values for all other keys
  4. Field defaults

The unusual precedence for JOB_CAP_* prevents a stray system-level env var
(e.g. a Windows User variable ``JOB_CAP_MEDIA=8``) from silently overriding
what the operator wrote in ``.env``.  All other settings follow the normal
pydantic-settings order where process env beats the dotenv file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Tuple, Type

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Repo layout: config.py → director_api/ → apps/api/ → apps/ → director/
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_APPS_API = Path(__file__).resolve().parent.parent

# Job-cap env keys whose .env value must win over the OS environment.
_JOB_CAP_ENV_KEYS: frozenset[str] = frozenset(
    {
        "JOB_CAPS_ENFORCED",
        "JOB_CAP_MEDIA",
        "JOB_CAP_COMPILE",
        "JOB_CAP_TEXT",
        "JOB_CAP_MEDIA_GLOBAL",
    }
)


def _parse_bool(val: str) -> bool:
    x = (val or "").strip().lower()
    return x in ("1", "true", "yes", "on")


class _DotenvJobCapSource(PydanticBaseSettingsSource):
    """Reads only JOB_CAP_* keys from the repo .env file.

    This source is placed *above* EnvSettingsSource in the chain so that
    values written in .env always win over OS environment variables for
    job-cap settings.  All other settings are not affected.
    """

    def __init__(self, settings_cls: Type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._values = self._load()

    def _load(self) -> dict[str, Any]:
        path = _REPO_ROOT / ".env"
        if not path.is_file():
            return {}
        raw: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, _, rest = s.partition("=")
            key = key.strip()
            if key in _JOB_CAP_ENV_KEYS:
                raw[key] = rest.strip().strip("'").strip('"')

        out: dict[str, Any] = {}
        if "JOB_CAPS_ENFORCED" in raw:
            out["job_caps_enforced"] = _parse_bool(raw["JOB_CAPS_ENFORCED"])
        for env_k, field_k, lo, hi in (
            ("JOB_CAP_MEDIA", "job_cap_media", 1, 100),
            ("JOB_CAP_COMPILE", "job_cap_compile", 1, 50),
            ("JOB_CAP_TEXT", "job_cap_text", 1, 100),
            ("JOB_CAP_MEDIA_GLOBAL", "job_cap_media_global", 1, 500),
        ):
            if env_k not in raw:
                continue
            try:
                n = int(raw[env_k].strip())
                out[field_k] = max(lo, min(hi, n))
            except ValueError:
                pass
        return out

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        if field_name in self._values:
            return self._values[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._values)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            _REPO_ROOT / ".env",
            _APPS_API / ".env",
            ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Core infrastructure
    # ------------------------------------------------------------------
    database_url: str = (
        "postgresql+psycopg://director:director_dev_change_me@localhost:5433/director"
    )
    db_pool_size: int = Field(
        default=5,
        ge=1,
        le=100,
        validation_alias=AliasChoices("db_pool_size", "DB_POOL_SIZE"),
    )
    db_max_overflow: int = Field(
        default=10,
        ge=0,
        le=200,
        validation_alias=AliasChoices("db_max_overflow", "DB_MAX_OVERFLOW"),
    )
    redis_url: str = "redis://localhost:6379/0"

    local_storage_root: str = ""
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"

    # Workspace whose app_settings JSON supplies optional API keys for users with
    # ``User.use_platform_api_credentials`` (set in admin). Empty = feature disabled.
    director_platform_credentials_source_tenant_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "director_platform_credentials_source_tenant_id",
            "DIRECTOR_PLATFORM_CREDENTIALS_SOURCE_TENANT_ID",
        ),
    )

    # ------------------------------------------------------------------
    # LLM providers
    # ------------------------------------------------------------------
    openai_api_key: str | None = None
    openai_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("openai_api_base_url", "OPENAI_API_BASE_URL"),
    )
    openai_compatible_text_source: str = Field(
        default="openai",
        validation_alias=AliasChoices(
            "openai_compatible_text_source",
            "OPENAI_COMPATIBLE_TEXT_SOURCE",
        ),
    )
    lm_studio_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("lm_studio_api_base_url", "LM_STUDIO_API_BASE_URL"),
    )
    lm_studio_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("lm_studio_api_key", "LM_STUDIO_API_KEY"),
    )
    lm_studio_text_model: str = Field(
        default="",
        validation_alias=AliasChoices("lm_studio_text_model", "LM_STUDIO_TEXT_MODEL"),
    )
    openai_local_chat_max_tokens: int = Field(
        default=16384,
        ge=512,
        le=200_000,
        validation_alias=AliasChoices("openai_local_chat_max_tokens", "OPENAI_LOCAL_CHAT_MAX_TOKENS"),
    )
    openai_timeout_sec: float = 120.0
    openai_tts_model: str = "tts-1"
    openai_tts_voice: str = "alloy"

    active_text_provider: str = "openai"
    active_image_provider: str = "fal"
    active_video_provider: str = "fal"
    active_speech_provider: str = "openai"

    # When true, worker uses placeholder lavfi images (and similar) instead of cloud image APIs where
    # applicable; narration still follows ``active_speech_provider`` / project preferred speech unless set to placeholder.
    # Default budget/smoke runs pin cheap project providers in the brief; production Studio runs use
    # workspace Settings (``active_*_provider``) and real image/video APIs — see ``--production-media`` on
    # ``scripts/budget_pipeline_test.py`` and ``production_media`` on ``POST /v1/admin/budget-pipeline-test``.
    director_placeholder_media: bool = Field(
        default=False,
        validation_alias=AliasChoices("director_placeholder_media", "DIRECTOR_PLACEHOLDER_MEDIA"),
    )

    openrouter_api_key: str | None = None
    xai_api_key: str | None = None
    grok_api_key: str | None = None
    fal_key: str | None = None
    tavily_api_key: str | None = None

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    xai_base_url: str = "https://api.x.ai/v1"

    openai_smoke_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("openai_smoke_model", "OPENAI_SMOKE_MODEL", "OPENAI_TEXT_MODEL"),
    )
    openai_image_model: str = "gpt-image-1"
    openrouter_smoke_model: str = "openai/gpt-4o-mini"
    xai_text_model: str = "grok-2-latest"
    grok_image_model: str = "grok-2-image-1212"
    grok_video_model: str = "grok-2-video"

    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("gemini_api_key", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    gemini_text_model: str = "gemini-2.0-flash"
    gemini_image_model: str = "imagen-4.0-generate-001"
    gemini_video_model: str = "veo-3.1-generate-preview"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_tts_voice: str = "Kore"

    # ------------------------------------------------------------------
    # ElevenLabs
    # ------------------------------------------------------------------
    elevenlabs_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("elevenlabs_api_key", "ELEVENLABS_API_KEY"),
    )
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_multilingual_v2"

    # ------------------------------------------------------------------
    # Kokoro local TTS
    # ------------------------------------------------------------------
    kokoro_voice: str = "af_bella"
    kokoro_lang_code: str = "a"
    kokoro_speed: float = 1.0
    kokoro_hf_repo_id: str = "hexgrad/Kokoro-82M"
    kokoro_device: str = ""  # cpu | cuda | mps | "" (auto)

    # ------------------------------------------------------------------
    # Chatterbox local TTS
    # ------------------------------------------------------------------
    chatterbox_voice_ref_path: str = ""
    chatterbox_mtl_language_id: str = "en"
    chatterbox_device: str = ""
    chatterbox_editable_path: str = Field(
        default="",
        validation_alias=AliasChoices("chatterbox_editable_path", "CHATTERBOX_EDITABLE_PATH"),
    )

    # When True and Kokoro/Chatterbox imports fail, auto-pip-install in the current env.
    tts_auto_pip_install: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "tts_auto_pip_install",
            "DIRECTOR_TTS_AUTO_PIP_INSTALL",
            "TTS_AUTO_PIP_INSTALL",
        ),
    )
    tts_auto_pip_timeout_sec: float = Field(
        default=1200.0,
        ge=60.0,
        le=7200.0,
        validation_alias=AliasChoices("tts_auto_pip_timeout_sec", "TTS_AUTO_PIP_TIMEOUT_SEC"),
    )
    kokoro_pip_kokoro_spec: str = "kokoro>=0.9.4"
    kokoro_pip_soundfile_spec: str = "soundfile>=0.12.1"

    # ------------------------------------------------------------------
    # fal.ai
    # ------------------------------------------------------------------
    fal_smoke_model: str = "fal-ai/fast-sdxl"
    fal_video_model: str = "fal-ai/minimax/video-01-live"

    # ------------------------------------------------------------------
    # ComfyUI
    # ------------------------------------------------------------------
    comfyui_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("comfyui_base_url", "COMFYUI_BASE_URL"),
    )
    comfyui_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "comfyui_api_key",
            "COMFYUI_API_KEY",
            "COMFY_CLOUD_API_KEY",
        ),
    )
    comfyui_api_flavor: str = Field(
        default="oss",
        validation_alias=AliasChoices("comfyui_api_flavor", "COMFYUI_API_FLAVOR"),
    )
    comfyui_workflow_json_path: str = Field(
        default="",
        validation_alias=AliasChoices("comfyui_workflow_json_path", "COMFYUI_WORKFLOW_JSON_PATH"),
    )
    comfyui_prompt_node_id: str = Field(
        default="",
        validation_alias=AliasChoices("comfyui_prompt_node_id", "COMFYUI_PROMPT_NODE_ID"),
    )
    comfyui_prompt_input_key: str = Field(
        default="text",
        validation_alias=AliasChoices("comfyui_prompt_input_key", "COMFYUI_PROMPT_INPUT_KEY"),
    )
    comfyui_negative_node_id: str = Field(
        default="",
        validation_alias=AliasChoices("comfyui_negative_node_id", "COMFYUI_NEGATIVE_NODE_ID"),
    )
    comfyui_default_negative_prompt: str = Field(
        default="",
        validation_alias=AliasChoices(
            "comfyui_default_negative_prompt",
            "COMFYUI_DEFAULT_NEGATIVE_PROMPT",
        ),
    )
    comfyui_model_name: str = Field(
        default="",
        validation_alias=AliasChoices("comfyui_model_name", "COMFYUI_MODEL_NAME"),
    )
    comfyui_timeout_sec: float = Field(
        default=900.0,
        ge=30.0,
        le=7200.0,
        validation_alias=AliasChoices("comfyui_timeout_sec", "COMFYUI_TIMEOUT_SEC"),
    )
    comfyui_poll_interval_sec: float = Field(
        default=1.0,
        ge=0.2,
        le=10.0,
        validation_alias=AliasChoices("comfyui_poll_interval_sec", "COMFYUI_POLL_INTERVAL_SEC"),
    )
    comfyui_video_workflow_json_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "comfyui_video_workflow_json_path",
            "COMFYUI_VIDEO_WORKFLOW_JSON_PATH",
        ),
    )
    comfyui_video_timeout_sec: float = Field(
        default=1800.0,
        ge=60.0,
        le=7200.0,
        validation_alias=AliasChoices("comfyui_video_timeout_sec", "COMFYUI_VIDEO_TIMEOUT_SEC"),
    )
    comfyui_video_model_name: str = Field(
        default="wan-2.1-comfyui",
        validation_alias=AliasChoices("comfyui_video_model_name", "COMFYUI_VIDEO_MODEL_NAME"),
    )
    comfyui_video_prompt_node_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "comfyui_video_prompt_node_id",
            "COMFYUI_VIDEO_PROMPT_NODE_ID",
        ),
    )
    comfyui_video_negative_node_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "comfyui_video_negative_node_id",
            "COMFYUI_VIDEO_NEGATIVE_NODE_ID",
        ),
    )
    comfyui_video_default_negative_prompt: str = Field(
        default="",
        validation_alias=AliasChoices(
            "comfyui_video_default_negative_prompt",
            "COMFYUI_VIDEO_DEFAULT_NEGATIVE_PROMPT",
        ),
    )
    comfyui_video_prompt_input_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "comfyui_video_prompt_input_key",
            "COMFYUI_VIDEO_PROMPT_INPUT_KEY",
        ),
    )
    comfyui_video_use_scene_image: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "comfyui_video_use_scene_image",
            "COMFYUI_VIDEO_USE_SCENE_IMAGE",
        ),
    )
    comfyui_video_load_image_node_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "comfyui_video_load_image_node_id",
            "COMFYUI_VIDEO_LOAD_IMAGE_NODE_ID",
        ),
    )

    # ------------------------------------------------------------------
    # Research
    # ------------------------------------------------------------------
    research_max_results: int = 5
    research_extract_chars: int = 2000
    research_http_timeout_sec: float = 12.0

    # ------------------------------------------------------------------
    # Runtime / pipeline behaviour
    # ------------------------------------------------------------------
    log_json: bool = True
    celery_eager: bool = False
    agent_run_fast: bool = False
    agent_run_pause_poll_sec: float = Field(
        default=2.0,
        ge=0.5,
        le=60.0,
        validation_alias=AliasChoices("agent_run_pause_poll_sec", "AGENT_RUN_PAUSE_POLL_SEC"),
        description="When an agent run is paused, re-queue the Celery task after this many seconds (solo-pool friendly).",
    )
    agent_run_chapter_critique_max_rounds: int = Field(default=5, ge=1, le=20)
    agent_run_scene_repair_max_rounds: int = Field(default=2, ge=0, le=8)
    agent_run_chapter_repair_max_rounds: int = Field(default=1, ge=0, le=5)
    agent_run_auto_generate_scene_videos: bool = False
    openai_agents_parallel: bool = True
    agent_oversight_llm_enabled: bool = True

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    webhook_url: str | None = None
    webhook_signing_secret: str | None = None
    webhook_timeout_sec: float = 30.0
    # Maximum delivery attempts per event (1 = no retry, 2+ = with backoff).
    webhook_max_attempts: int = Field(default=3, ge=1, le=10)

    # Telegram bot (optional): Settings UI + Bot API webhook for hands-off runs and notifications.
    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
    )
    telegram_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("telegram_chat_id", "TELEGRAM_CHAT_ID"),
    )
    # Must match Telegram setWebhook `secret_token`; sent as `X-Telegram-Bot-Api-Secret-Token`.
    telegram_webhook_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("telegram_webhook_secret", "TELEGRAM_WEBHOOK_SECRET"),
    )
    # Notify on failed / cancelled / blocked runs (Telegram); includes Retry when possible.
    telegram_notify_pipeline_failures: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "telegram_notify_pipeline_failures",
            "TELEGRAM_NOTIFY_PIPELINE_FAILURES",
        ),
    )
    # Absolute Studio URL for Telegram deep links (e.g. https://studio.example.com).
    director_public_app_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("director_public_app_url", "DIRECTOR_PUBLIC_APP_URL"),
    )
    # Public API base for OAuth redirects (e.g. https://api.example.com). If unset, auth-url uses the incoming request host.
    public_api_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("public_api_base_url", "PUBLIC_API_BASE_URL"),
    )
    # YouTube Data API v3 (optional). Refresh token is stored per workspace in app_settings.
    youtube_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("youtube_client_id", "YOUTUBE_CLIENT_ID"),
    )
    youtube_client_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("youtube_client_secret", "YOUTUBE_CLIENT_SECRET"),
    )
    youtube_refresh_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("youtube_refresh_token", "YOUTUBE_REFRESH_TOKEN"),
    )
    youtube_auto_upload_after_export: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "youtube_auto_upload_after_export",
            "YOUTUBE_AUTO_UPLOAD_AFTER_EXPORT",
        ),
    )
    youtube_default_privacy: str = Field(
        default="unlisted",
        validation_alias=AliasChoices("youtube_default_privacy", "YOUTUBE_DEFAULT_PRIVACY"),
        description='Video privacy: "public", "unlisted", or "private".',
    )
    youtube_share_watch_link_in_telegram: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "youtube_share_watch_link_in_telegram",
            "YOUTUBE_SHARE_WATCH_LINK_IN_TELEGRAM",
        ),
    )
    # When true, automated final_cut jobs burn subtitles.vtt into the MP4 when the file exists.
    burn_subtitles_in_final_cut_default: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "burn_subtitles_in_final_cut_default",
            "BURN_SUBTITLES_IN_FINAL_CUT_DEFAULT",
        ),
    )

    # ------------------------------------------------------------------
    # Phase 4 — critic
    # ------------------------------------------------------------------
    critic_pass_threshold: float = 0.55
    critic_max_revision_cycles_per_scene: int = 5
    chapter_min_scene_pass_ratio: float = 0.85
    chapter_pass_score_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    critic_missing_dimension_default: float = Field(default=0.6, ge=0.0, le=1.0)

    # ------------------------------------------------------------------
    # Phase 5 — FFmpeg
    # ------------------------------------------------------------------
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ffmpeg_compile_enabled: bool = True
    ffmpeg_slideshow_default_sec: float = 3.0
    scene_clip_duration_sec: int = Field(default=10, ge=5, le=10, description="5 or 10 seconds")
    scene_plan_target_scenes_per_chapter: int = Field(default=0, ge=0, le=48)
    ffmpeg_output_width: int = 1280
    ffmpeg_output_height: int = 720
    ffmpeg_timeout_sec: float = 3600.0
    export_chapter_title_card_sec: float = Field(default=0.0, ge=0.0, le=30.0)
    scene_vo_tail_padding_sec: float = Field(
        default=1.5,
        ge=0.0,
        le=120.0,
        description="Silence/hold after spoken VO before next beat (export, planned_duration bumps, timeline slots).",
        validation_alias=AliasChoices("scene_vo_tail_padding_sec", "SCENE_VO_TAIL_PADDING_SEC"),
    )

    # ------------------------------------------------------------------
    # Phase 6 — job concurrency caps
    # ------------------------------------------------------------------
    job_caps_enforced: bool = False
    job_cap_media: int = Field(default=8, ge=1, le=100)
    job_cap_compile: int = Field(default=2, ge=1, le=50)
    job_cap_text: int = Field(default=5, ge=1, le=100)
    job_cap_media_global: int = Field(default=20, ge=1, le=500)
    stale_job_minutes: int = 45

    # ------------------------------------------------------------------
    # API / rate limiting
    # ------------------------------------------------------------------
    api_rate_limit_per_minute: int = 120
    api_celery_restart_rate_limit_per_minute: int = Field(
        default=3,
        ge=1,
        le=120,
        validation_alias=AliasChoices(
            "api_celery_restart_rate_limit_per_minute",
            "API_CELERY_RESTART_RATE_LIMIT_PER_MINUTE",
        ),
    )
    rate_limit_enabled: bool = True
    rate_limit_relax_loopback: bool = True
    cors_extra_origins: str = ""

    # ------------------------------------------------------------------
    # Multi-tenant auth (optional; default off for local single-tenant)
    # ------------------------------------------------------------------
    director_auth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("director_auth_enabled", "DIRECTOR_AUTH_ENABLED"),
    )
    usage_credits_enforce: bool = Field(
        default=False,
        validation_alias=AliasChoices("usage_credits_enforce", "USAGE_CREDITS_ENFORCE"),
        description="Deprecated: credit enforcement uses entitlements credits_enforce + monthly_credits (admin/plans). Ignored.",
    )
    director_jwt_secret: str = Field(
        default="",
        validation_alias=AliasChoices("director_jwt_secret", "DIRECTOR_JWT_SECRET"),
    )
    director_jwt_expire_hours: int = Field(default=168, ge=1, le=8760)
    director_jwt_reject_weak_secret: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "director_jwt_reject_weak_secret",
            "DIRECTOR_JWT_REJECT_WEAK_SECRET",
        ),
        description="When auth is enabled, refuse API startup if JWT secret is missing or trivially weak.",
    )
    runtime_settings_cache_ttl_sec: float = Field(
        default=15.0,
        ge=0.0,
        le=3600.0,
        validation_alias=AliasChoices(
            "runtime_settings_cache_ttl_sec",
            "RUNTIME_SETTINGS_CACHE_TTL_SEC",
        ),
    )
    director_session_cookie_name: str = Field(
        default="director_session",
        validation_alias=AliasChoices("director_session_cookie_name", "DIRECTOR_SESSION_COOKIE_NAME"),
    )
    director_session_ttl_seconds: int = Field(
        default=2_592_000,  # 30 days
        ge=300,
        le=31536000,
        validation_alias=AliasChoices("director_session_ttl_seconds", "DIRECTOR_SESSION_TTL_SECONDS"),
        description="Redis-backed browser session lifetime for HttpOnly director_session cookie.",
    )
    director_session_cookie_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("director_session_cookie_secure", "DIRECTOR_SESSION_COOKIE_SECURE"),
        description="Set Secure flag on session cookie (required for HTTPS production).",
    )
    director_session_cookie_samesite: str = Field(
        default="lax",
        validation_alias=AliasChoices("director_session_cookie_samesite", "DIRECTOR_SESSION_COOKIE_SAMESITE"),
        description="Starlette samesite value: lax, strict, or none (none requires Secure).",
    )
    director_query_jwt_expire_minutes: int = Field(
        default=60,
        ge=5,
        le=24 * 60,
        validation_alias=AliasChoices("director_query_jwt_expire_minutes", "DIRECTOR_QUERY_JWT_EXPIRE_MINUTES"),
        description="Short-lived JWT returned for media/SSE query params (not the HttpOnly session).",
    )
    director_allow_registration: bool = Field(
        default=True,
        validation_alias=AliasChoices("director_allow_registration", "DIRECTOR_ALLOW_REGISTRATION"),
        description="When auth is enabled, allow POST /v1/auth/register for new workspaces.",
    )
    # Firebase Auth (Google sign-in): server verifies ID tokens; web needs matching client config.
    director_firebase_project_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "director_firebase_project_id",
            "DIRECTOR_FIREBASE_PROJECT_ID",
            "FIREBASE_PROJECT_ID",
        ),
    )
    director_firebase_credentials_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "director_firebase_credentials_path",
            "DIRECTOR_FIREBASE_CREDENTIALS_PATH",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ),
        description="Path to Firebase service account JSON (for verify_id_token).",
    )
    director_firebase_web_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "director_firebase_web_api_key",
            "DIRECTOR_FIREBASE_WEB_API_KEY",
        ),
    )
    director_firebase_web_auth_domain: str = Field(
        default="",
        validation_alias=AliasChoices(
            "director_firebase_web_auth_domain",
            "DIRECTOR_FIREBASE_WEB_AUTH_DOMAIN",
        ),
    )
    director_firebase_web_app_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "director_firebase_web_app_id",
            "DIRECTOR_FIREBASE_WEB_APP_ID",
        ),
    )

    stripe_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("stripe_secret_key", "STRIPE_SECRET_KEY"),
    )
    stripe_webhook_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET"),
    )
    stripe_publishable_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("stripe_publishable_key", "STRIPE_PUBLISHABLE_KEY"),
    )
    billing_success_url: str = Field(
        default="http://localhost:5173/?billing=success",
        validation_alias=AliasChoices("billing_success_url", "BILLING_SUCCESS_URL"),
    )
    billing_cancel_url: str = Field(
        default="http://localhost:5173/?billing=cancel",
        validation_alias=AliasChoices("billing_cancel_url", "BILLING_CANCEL_URL"),
    )
    stripe_price_studio_monthly: str | None = Field(
        default=None,
        validation_alias=AliasChoices("stripe_price_studio_monthly", "STRIPE_PRICE_STUDIO_MONTHLY"),
        description="Stripe Price id for the default studio_monthly plan (seeded into subscription_plans).",
    )

    director_ops_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("director_ops_api_key", "DIRECTOR_OPS_API_KEY"),
        description="When auth is enabled, metrics and celery restart require X-Director-Ops-Key matching this value.",
    )
    director_admin_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("director_admin_api_key", "DIRECTOR_ADMIN_API_KEY"),
        description="Platform admin API + UI: require X-Director-Admin-Key matching this value.",
    )

    director_expose_openapi: bool = Field(
        default=True,
        validation_alias=AliasChoices("director_expose_openapi", "DIRECTOR_EXPOSE_OPENAPI"),
    )

    # ------------------------------------------------------------------
    # Style presets
    # ------------------------------------------------------------------
    narration_style_preset: str = "narrative_documentary"
    default_narration_style_ref: str | None = Field(
        default=None,
        description="Workspace default narration ref: preset:<id> or user:<uuid> for new runs / empty project field.",
    )
    visual_style_preset: str = "cinematic_documentary"
    visual_preset_overrides: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Studio UI
    # ------------------------------------------------------------------
    studio_batch_image_interval_sec: int = Field(default=5, ge=2, le=3600)
    studio_job_poll_interval_ms: int = Field(default=800, ge=500, le=120_000)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("scene_clip_duration_sec")
    @classmethod
    def scene_clip_only_five_or_ten(cls, v: int) -> int:
        if int(v) not in (5, 10):
            raise ValueError("scene_clip_duration_sec must be 5 or 10")
        return int(v)

    @field_validator("narration_style_preset")
    @classmethod
    def narration_preset_known(cls, v: str) -> str:
        from director_api.style_presets import DEFAULT_NARRATION_PRESET, is_valid_narration_preset

        s = (v or "").strip()
        return s if is_valid_narration_preset(s) else DEFAULT_NARRATION_PRESET

    @field_validator("default_narration_style_ref")
    @classmethod
    def default_narration_style_ref_ok(cls, v: str | None) -> str | None:
        from director_api.style_presets import sanitize_default_narration_style_ref

        return sanitize_default_narration_style_ref(v)

    @field_validator("visual_style_preset")
    @classmethod
    def visual_preset_known(cls, v: str) -> str:
        from director_api.style_presets import DEFAULT_VISUAL_PRESET, is_valid_visual_preset

        s = (v or "").strip()
        return s if is_valid_visual_preset(s) else DEFAULT_VISUAL_PRESET

    @field_validator("comfyui_api_flavor")
    @classmethod
    def comfyui_api_flavor_normalize(cls, v: str) -> str:
        s = (v or "oss").strip().lower()
        if s in ("cloud", "comfy_cloud", "comfy-cloud"):
            return "cloud"
        return "oss"

    @model_validator(mode="after")
    def _resolve_local_storage_root(self):
        """Resolve local_storage_root to an absolute path relative to the repo root."""
        raw = (self.local_storage_root or "").strip()
        if not raw:
            root = (_REPO_ROOT / "data" / "storage").resolve()
        else:
            p = Path(raw)
            # Relative paths must not depend on cwd (API vs Celery can differ on Windows).
            root = (_REPO_ROOT / p).resolve() if not p.is_absolute() else p.resolve()
        object.__setattr__(self, "local_storage_root", str(root))
        return self

    # ------------------------------------------------------------------
    # Custom settings source ordering
    # ------------------------------------------------------------------

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: EnvSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Place _DotenvJobCapSource above env_settings so JOB_CAP_* from .env wins over OS env."""
        return (
            init_settings,
            _DotenvJobCapSource(settings_cls),  # .env job caps beat OS env
            env_settings,                        # other OS env vars
            dotenv_settings,                     # .env everything else
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
