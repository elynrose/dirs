"""Tests for Telegram webhook tenant resolution (no live Telegram API)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from director_api.config import Settings

TENANT_A = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TENANT_B = "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee"


def _rt_for_tenant(tid: str, *, chat: str, secret: str = "whsec") -> Settings:
    s = Settings()
    return s.model_copy(
        update={
            "default_tenant_id": tid,
            "telegram_chat_id": chat,
            "telegram_bot_token": "bot-token",
            "telegram_webhook_secret": secret,
        }
    )


def test_find_runtime_picks_tenant_matching_chat_and_secret():
    from director_api.api.routers.integrations_telegram import _find_runtime_settings_for_telegram_chat

    app_a = MagicMock()
    app_a.tenant_id = TENANT_A
    app_b = MagicMock()
    app_b.tenant_id = TENANT_B

    base = Settings()
    base = base.model_copy(update={"default_tenant_id": TENANT_A})

    def fake_resolve(_db, _base, tid):
        if tid == TENANT_A:
            return _rt_for_tenant(TENANT_A, chat="111")
        if tid == TENANT_B:
            return _rt_for_tenant(TENANT_B, chat="222")
        raise AssertionError(tid)

    db = MagicMock()
    db.scalars.return_value.all.return_value = [app_a, app_b]

    with patch(
        "director_api.api.routers.integrations_telegram.resolve_runtime_settings",
        side_effect=fake_resolve,
    ):
        rt = _find_runtime_settings_for_telegram_chat(
            db, base, incoming_chat_id="222", secret_header="whsec"
        )
    assert rt is not None
    assert rt.default_tenant_id == TENANT_B
    assert rt.telegram_chat_id == "222"


def test_find_runtime_returns_none_on_secret_mismatch():
    from director_api.api.routers.integrations_telegram import _find_runtime_settings_for_telegram_chat

    app_b = MagicMock()
    app_b.tenant_id = TENANT_B
    base = Settings()
    base = base.model_copy(update={"default_tenant_id": TENANT_A})

    def fake_resolve(_db, _base, tid):
        if tid == TENANT_B:
            return _rt_for_tenant(TENANT_B, chat="222", secret="good")
        return _rt_for_tenant(tid, chat="999")

    db = MagicMock()
    db.scalars.return_value.all.return_value = [app_b]

    with patch(
        "director_api.api.routers.integrations_telegram.resolve_runtime_settings",
        side_effect=fake_resolve,
    ):
        rt = _find_runtime_settings_for_telegram_chat(
            db, base, incoming_chat_id="222", secret_header="wrong"
        )
    assert rt is None


def test_find_runtime_falls_back_to_default_tenant_id():
    from director_api.api.routers.integrations_telegram import _find_runtime_settings_for_telegram_chat

    app_a = MagicMock()
    app_a.tenant_id = TENANT_A
    base = Settings()
    base = base.model_copy(update={"default_tenant_id": TENANT_B})

    def fake_resolve(_db, _base, tid):
        if tid == TENANT_A:
            return _rt_for_tenant(TENANT_A, chat="111")
        if tid == TENANT_B:
            return _rt_for_tenant(TENANT_B, chat="222")
        raise AssertionError(tid)

    db = MagicMock()
    db.scalars.return_value.all.return_value = [app_a]

    with patch(
        "director_api.api.routers.integrations_telegram.resolve_runtime_settings",
        side_effect=fake_resolve,
    ):
        rt = _find_runtime_settings_for_telegram_chat(
            db, base, incoming_chat_id="222", secret_header="whsec"
        )
    assert rt is not None
    assert rt.default_tenant_id == TENANT_B


def test_effective_webhook_secret_falls_back_to_base():
    from director_api.api.routers.integrations_telegram import _effective_webhook_secret

    base = Settings()
    base = base.model_copy(update={"telegram_webhook_secret": "from-env"})
    rt = Settings()
    rt = rt.model_copy(update={"telegram_webhook_secret": None})
    assert _effective_webhook_secret(rt, base) == "from-env"
