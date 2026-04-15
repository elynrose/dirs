"""Settings PATCH merge and GET redaction for credential keys."""

from director_api.services.runtime_settings import (
    merge_app_settings_config_patch,
    redact_settings_config_for_api,
)


def test_redact_strips_secrets_and_presence() -> None:
    cfg, pres = redact_settings_config_for_api(
        {
            "openai_api_key": "sk-secret",
            "active_text_provider": "openai",
            "fal_key": "",
            "comfyui_api_key": "comfy-secret",
        }
    )
    assert "openai_api_key" not in cfg
    assert pres.get("openai_api_key") is True
    assert "fal_key" not in pres
    assert "comfyui_api_key" not in cfg
    assert pres.get("comfyui_api_key") is True
    assert cfg.get("active_text_provider") == "openai"


def test_merge_patch_empty_secret_preserves_prior() -> None:
    prior = {"openai_api_key": "stored", "foo": 1}
    patch = {"openai_api_key": "", "foo": 2}
    out = merge_app_settings_config_patch(prior, patch)
    assert out["openai_api_key"] == "stored"
    assert out["foo"] == 2


def test_merge_patch_null_clears_secret() -> None:
    prior = {"openai_api_key": "stored"}
    out = merge_app_settings_config_patch(prior, {"openai_api_key": None})
    assert "openai_api_key" not in out


def test_merge_patch_new_secret_value() -> None:
    prior = {"openai_api_key": "old"}
    out = merge_app_settings_config_patch(prior, {"openai_api_key": "newkey"})
    assert out["openai_api_key"] == "newkey"


def test_merge_patch_empty_comfyui_key_preserves_prior() -> None:
    prior = {"comfyui_api_key": "stored"}
    out = merge_app_settings_config_patch(prior, {"comfyui_api_key": ""})
    assert out["comfyui_api_key"] == "stored"
