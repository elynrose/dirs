"""POST /comfyui-workflows/test must not match the upload route (role=test)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from director_api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_comfyui_workflows_test_route_accepts_json_body(client: TestClient) -> None:
    r = client.post("/v1/settings/comfyui-workflows/test", json={"mode": "connection"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["mode"] == "connection"
