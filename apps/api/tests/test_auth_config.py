def test_auth_config_route_registered():
    from director_api.api.routers.auth import router

    paths = [getattr(r, "path", "") for r in router.routes]
    assert "/auth/config" in paths
    assert "/auth/me" in paths
    assert "/auth/change-password" in paths


def test_require_project_for_tenant():
    import uuid
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import pytest
    from fastapi import HTTPException

    from director_api.api.tenant_access import get_project_for_tenant, require_project_for_tenant

    pid = uuid.uuid4()
    tenant_ok = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    other = "00000000-0000-0000-0000-000000000099"
    proj = SimpleNamespace(id=pid, tenant_id=tenant_ok)
    db = MagicMock()
    db.get.return_value = proj

    assert get_project_for_tenant(db, pid, tenant_ok) is proj
    assert get_project_for_tenant(db, pid, other) is None

    assert require_project_for_tenant(db, pid, tenant_ok) is proj
    with pytest.raises(HTTPException) as exc:
        require_project_for_tenant(db, pid, other)
    assert exc.value.status_code == 404


def test_resolve_runtime_settings_tenant_override():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from director_api.config import Settings
    from director_api.services.runtime_settings import resolve_runtime_settings

    base = Settings()
    db = MagicMock()
    row = SimpleNamespace(config_json={"openai_timeout_sec": 99.0})
    db.query.return_value.filter.return_value.one_or_none.return_value = row

    out = resolve_runtime_settings(db, base, tenant_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert out.default_tenant_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert float(out.openai_timeout_sec) == 99.0
