"""HTTP tests for GET /v1/scenes/{id}/resolved-prompts."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from director_api.main import app
from director_api.services.narration_bracket_visual import video_text_prompt_from_scene_fields


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_video_prompt_bracket_parity_with_service():
    t = video_text_prompt_from_scene_fields(
        narration_text="The [volcano] glowed.",
        purpose=None,
        visual_type=None,
        prompt_package_json={},
        video_prompt_override=None,
    )
    assert "volcano" in t.lower()


def test_resolved_prompts_endpoint_returns_worker_prompts(client: TestClient):
    from director_api.db.session import get_db

    scene_id = uuid.uuid4()
    chapter_id = uuid.uuid4()
    project_id = uuid.uuid4()
    scene = SimpleNamespace(
        id=scene_id,
        chapter_id=chapter_id,
        narration_text="The [volcano] glowed.",
        prompt_package_json={},
        purpose=None,
        visual_type=None,
    )
    chapter = SimpleNamespace(id=chapter_id, project_id=project_id)
    project = SimpleNamespace(id=project_id, tenant_id="00000000-0000-0000-0000-000000000001")

    def fake_get(model, pk):
        if model.__name__ == "Scene":
            return scene if pk == scene_id else None
        if model.__name__ == "Chapter":
            return chapter if pk == chapter_id else None
        if model.__name__ == "Project":
            return project if pk == project_id else None
        return None

    db = MagicMock()
    db.get.side_effect = fake_get

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with (
            patch(
                "director_api.api.routers.workflow_phase3._scene_still_prompt_for_comfy",
                return_value="still: volcano tableau",
            ),
            patch(
                "director_api.api.routers.workflow_phase3._resolve_phase3_video_text_prompt",
                return_value="video: volcano motion",
            ),
        ):
            r = client.get(f"/v1/scenes/{scene_id}/resolved-prompts")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["scene_id"] == str(scene_id)
    assert "volcano" in data["image_prompt"].lower()
    assert "volcano" in data["video_prompt"].lower()


@pytest.mark.parametrize(
    "steps,expect_substr",
    [
        (
            [{"step": "auto_videos", "status": "partial_failed", "generated": 0, "failure_reason_summary": "no videos"}],
            "no videos",
        ),
        (
            [{"step": "auto_timeline", "status": "visual_heal", "summary": "emergency image"}],
            "emergency image",
        ),
    ],
)
def test_warning_copy_from_steps(steps, expect_substr):
    from director_api.services.agent_run_warning_copy import summarize_agent_run_warnings

    out = summarize_agent_run_warnings(steps)
    assert expect_substr in out.lower()
