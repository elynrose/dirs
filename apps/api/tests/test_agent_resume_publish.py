"""Skip rules for thumbnail, opening hook, and optional outro pipeline steps."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from director_api.services import agent_resume as ar
from director_api.services.publish_hook import HOOK_SCENE_ROLE
from director_api.services.publish_outro import OUTRO_SCENE_ROLE


def _project(**kwargs):
    p = MagicMock()
    p.id = kwargs.get("id", uuid4())
    p.workflow_phase = kwargs.get("workflow_phase", "chapters_ready")
    p.publish_pack_json = kwargs.get("publish_pack_json")
    p.opening_hook_text = kwargs.get("opening_hook_text")
    p.include_outro_scene = kwargs.get("include_outro_scene", False)
    return p


def test_should_skip_thumbnail_when_pack_complete() -> None:
    p = _project(
        publish_pack_json={
            "youtube_title": "Great Video",
            "thumbnail_storage_key": "assets/t/p/thumb.png",
        }
    )
    assert ar.should_skip_thumbnail(True, p) is True


def test_should_not_skip_thumbnail_when_phase_only_without_image() -> None:
    p = _project(workflow_phase="thumbnail_ready", publish_pack_json={"youtube_title": "T"})
    assert ar.should_skip_thumbnail(True, p) is False


def test_should_not_skip_thumbnail_on_fresh_run() -> None:
    p = _project(publish_pack_json=None)
    assert ar.should_skip_thumbnail(False, p) is False


def test_should_skip_opening_hook_when_text_long_enough() -> None:
    p = _project(opening_hook_text="x" * 60)
    assert ar.should_skip_opening_hook(True, p) is True


def test_should_skip_opening_hook_when_phase_rank() -> None:
    p = _project(workflow_phase="hook_ready", opening_hook_text=None)
    assert ar.should_skip_opening_hook(True, p) is True


def test_should_skip_hook_scene_when_synced() -> None:
    p = _project(opening_hook_text="x" * 60)
    db = MagicMock()
    sc = MagicMock()
    sc.id = uuid4()
    sc.narration_text = p.opening_hook_text
    with patch("director_api.services.publish_hook.find_hook_scene", return_value=sc):
        db.scalar.return_value = 1
        assert ar.should_skip_hook_scene(True, p, db) is True


def test_should_skip_hook_scene_when_text_too_short() -> None:
    p = _project(opening_hook_text="short")
    db = MagicMock()
    assert ar.should_skip_hook_scene(True, p, db) is True


def test_should_skip_outro_when_disabled() -> None:
    p = _project(include_outro_scene=False)
    db = MagicMock()
    assert ar.should_skip_outro(True, p, db, include_outro=False) is True


def test_should_skip_outro_when_scene_exists() -> None:
    p = _project(include_outro_scene=True)
    sc = MagicMock()
    sc.narration_text = "Please subscribe for more videos like this one."
    sc.prompt_package_json = {"scene_role": OUTRO_SCENE_ROLE}
    db = MagicMock()
    with patch("director_api.services.publish_outro.find_outro_scene", return_value=sc):
        assert ar.should_skip_outro(True, p, db, include_outro=True) is True


def test_workflow_phase_rank_includes_publish_phases() -> None:
    assert ar.workflow_phase_rank("thumbnail_ready") == 6
    assert ar.workflow_phase_rank("hook_ready") == 7
    assert ar.workflow_phase_rank("scenes_planned") == 8
    assert ar.workflow_phase_rank("outro_ready") == 9
    assert ar.workflow_phase_rank("critique_complete") == 11
