"""ComfyUI workflow test async runs."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from director_api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_comfyui_video_test_returns_202_and_polls(client: TestClient) -> None:
    with patch(
        "director_api.services.comfyui_test_runs.generate_scene_video_comfyui",
        return_value={
            "ok": True,
            "provider": "comfyui_wan",
            "bytes": b"fake-mp4",
            "content_type": "video/mp4",
            "model": "wan-test",
        },
    ):
        start = client.post("/v1/settings/comfyui-workflows/test", json={"mode": "video"})
        assert start.status_code == 202, start.text
        test_id = start.json()["data"]["test_id"]
        assert start.json()["data"]["status"] == "running"

        import time

        deadline = time.time() + 5.0
        final = None
        while time.time() < deadline:
            poll = client.get(f"/v1/settings/comfyui-workflows/test/{test_id}")
            assert poll.status_code == 200, poll.text
            final = poll.json()["data"]
            if final.get("status") != "running":
                break
            time.sleep(0.05)

        assert final is not None
        assert final["status"] == "succeeded"
        assert final["ok"] is True
        assert final["bytes_written"] == len(b"fake-mp4")
