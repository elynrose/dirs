"""Opening hook scene 0 — append, thumbnail still, replan preserve."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from director_api.db.models import Scene
from director_api.services import agent_resume as agent_resume_svc
from director_api.services.publish_hook import HOOK_SCENE_ROLE, is_hook_scene


def test_is_hook_scene_role() -> None:
    sc = Scene(id=uuid.uuid4(), chapter_id=uuid.uuid4(), order_index=0, prompt_package_json={"scene_role": "hook"})
    assert is_hook_scene(sc) is True
    sc2 = Scene(id=uuid.uuid4(), chapter_id=uuid.uuid4(), order_index=0, prompt_package_json={})
    assert is_hook_scene(sc2) is False


def test_append_hook_scene_shifts_existing_scenes() -> None:
    from director_api.services.publish_hook import append_hook_scene

    pid = uuid.uuid4()
    cid = uuid.uuid4()
    project = MagicMock()
    project.id = pid
    project.tenant_id = "t1"
    project.title = "Test"
    project.topic = "topic"
    project.visual_style = "doc"
    project.opening_hook_text = "This is a compelling opening hook for the documentary."
    project.publish_pack_json = {"thumbnail_prompt": "cover art", "thumbnail_storage_key": "assets/t/p/x.png"}

    chapter = MagicMock()
    chapter.id = cid
    s1 = MagicMock()
    s1.order_index = 0
    s2 = MagicMock()
    s2.order_index = 1

    db = MagicMock()
    db.scalars.return_value.first.side_effect = [chapter, [s2, s1]]
    db.scalars.return_value.all.return_value = [s1, s2]
    db.get.return_value = None

    settings = MagicMock()
    settings.local_storage_root = "/tmp"

    with patch("director_api.services.publish_hook.find_hook_scene", return_value=None):
        with patch(
            "director_api.services.publish_hook.attach_publish_thumbnail_to_hook_scene",
            return_value=MagicMock(),
        ):
            hook = append_hook_scene(db, project, settings)

    assert hook is not None
    assert hook.order_index == 0
    assert hook.prompt_package_json["scene_role"] == HOOK_SCENE_ROLE
    assert s1.order_index == 1
    assert s2.order_index == 2


def test_should_skip_hook_scene_when_synced() -> None:
    project = MagicMock()
    project.opening_hook_text = "x" * 60
    sc = MagicMock()
    sc.id = uuid.uuid4()
    sc.narration_text = project.opening_hook_text
    db = MagicMock()
    with patch("director_api.services.publish_hook.find_hook_scene", return_value=sc):
        db.scalar.return_value = 1
        assert agent_resume_svc.should_skip_hook_scene(True, project, db) is True


def test_should_not_skip_hook_scene_when_narration_changed() -> None:
    project = MagicMock()
    project.opening_hook_text = "x" * 60
    sc = MagicMock()
    sc.narration_text = "different hook"
    db = MagicMock()
    with patch("director_api.services.publish_hook.find_hook_scene", return_value=sc):
        assert agent_resume_svc.should_skip_hook_scene(True, project, db) is False
