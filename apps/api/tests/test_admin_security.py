"""Unit tests for platform admin authentication helpers (no DB)."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from director_api.api.security_admin import assert_admin_request


def test_assert_admin_not_configured():
    from unittest.mock import patch

    req = MagicMock(spec=Request)
    req.headers = {}

    with patch("director_api.api.security_admin.get_settings") as gs:
        gs.return_value.director_admin_api_key = None
        with pytest.raises(HTTPException) as exc:
            assert_admin_request(req)
        assert exc.value.status_code == 503
        assert "ADMIN_NOT_CONFIGURED" in str(exc.value.detail)


def test_assert_admin_unauthorized_wrong_key():
    from unittest.mock import patch

    req = MagicMock(spec=Request)
    req.headers = {"x-director-admin-key": "wrong"}

    with patch("director_api.api.security_admin.get_settings") as gs:
        gs.return_value.director_admin_api_key = "secret"
        with pytest.raises(HTTPException) as exc:
            assert_admin_request(req)
        assert exc.value.status_code == 401


def test_assert_admin_ok():
    from unittest.mock import patch

    req = MagicMock(spec=Request)
    req.headers = {"X-Director-Admin-Key": "secret"}

    with patch("director_api.api.security_admin.get_settings") as gs:
        gs.return_value.director_admin_api_key = "secret"
        assert_admin_request(req) is None


def test_admin_health_route_exists():
    from director_api.api.routers import admin_api

    paths = [getattr(r, "path", "") for r in admin_api.router.routes]
    assert "/admin/health" in paths
    assert "/admin/entitlement-definitions" in paths
