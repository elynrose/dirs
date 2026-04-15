"""Platform credential inheritance + runtime settings cache invalidation."""

from unittest.mock import MagicMock

from director_api.config import Settings
from director_api.services import runtime_settings as rs


def test_tenant_may_inherit_auth_off_allows_merge_for_worker_without_user_flag() -> None:
    """Celery uses ``user_id=None``; legacy auth-off must not require a lone flagged user."""
    src = "11111111-1111-1111-1111-111111111111"
    child = "22222222-2222-2222-2222-222222222222"
    base = Settings(
        director_auth_enabled=False,
        director_platform_credentials_source_tenant_id=src,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    assert rs.tenant_may_inherit_platform_api_credentials(db, base, child, None) is True


def test_tenant_may_inherit_auth_off_source_tenant_no_merge() -> None:
    src = "11111111-1111-1111-1111-111111111111"
    base = Settings(
        director_auth_enabled=False,
        director_platform_credentials_source_tenant_id=src,
    )
    db = MagicMock()
    assert rs.tenant_may_inherit_platform_api_credentials(db, base, src, None) is False


def test_invalidate_after_write_on_platform_source_clears_entire_runtime_cache() -> None:
    rs._RUNTIME_SETTINGS_CACHE.clear()
    rs._RUNTIME_SETTINGS_CACHE["22222222-2222-2222-2222-222222222222\x1fnone\x1f1"] = (0.0, Settings())
    base = MagicMock()
    base.director_platform_credentials_source_tenant_id = "11111111-1111-1111-1111-111111111111"
    rs.invalidate_runtime_settings_cache_after_tenant_config_persisted(
        base, "11111111-1111-1111-1111-111111111111"
    )
    assert rs._RUNTIME_SETTINGS_CACHE == {}
