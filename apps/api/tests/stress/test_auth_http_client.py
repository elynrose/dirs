"""HTTP-level auth / health checks (in-process TestClient — no running server)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from director_api.main import app

pytestmark = pytest.mark.stress


def test_health_returns_ok() -> None:
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("data", {}).get("status") == "ok"


def test_auth_config_envelope() -> None:
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/v1/auth/config")
    assert r.status_code == 200
    data = r.json().get("data") or {}
    assert "auth_enabled" in data
