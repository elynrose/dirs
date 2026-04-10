"""Unit tests for pipeline oversight (resume routing)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from director_api.services import pipeline_oversight as po


def test_merge_earliest_prefers_earlier_stage() -> None:
    assert po.merge_earliest_steps("auto_images", "scenes") == "scenes"
    assert po.merge_earliest_steps("auto_images", "auto_characters") == "auto_characters"
    assert po.merge_earliest_steps("auto_characters", "auto_images") == "auto_characters"
    assert po.merge_earliest_steps("chapters", None) == "chapters"
    assert po.merge_earliest_steps(None, "outline") == "outline"
    assert po.merge_earliest_steps(None, None) is None


def test_merge_oversight_with_rerun_anchor() -> None:
    assert po.merge_oversight_with_rerun_anchor(None, "scenes") == "scenes"
    assert po.merge_oversight_with_rerun_anchor("auto_final_cut", "scenes") == "scenes"
    assert po.merge_oversight_with_rerun_anchor(None, "auto_narration") == "auto_narration"
    assert po.merge_oversight_with_rerun_anchor("outline", None) == "outline"


def test_clamp_oversight_floor_pins_user_chosen_phase() -> None:
    """LLM or deterministic gap cannot override the step the user chose to run."""
    assert po.clamp_oversight_floor("director", "auto_images") == "auto_images"
    assert po.clamp_oversight_floor("outline", "auto_images") == "auto_images"
    assert po.clamp_oversight_floor("auto_images", "auto_images") == "auto_images"
    assert po.clamp_oversight_floor("auto_narration", "auto_images") == "auto_images"
    assert po.clamp_oversight_floor("auto_narration", "auto_characters") == "auto_characters"
    assert po.clamp_oversight_floor("auto_final_cut", "scenes") == "scenes"


def test_oversight_blocks_resume_skip() -> None:
    assert po.oversight_blocks_resume_skip("scenes", "director") is False
    assert po.oversight_blocks_resume_skip("scenes", "scenes") is True
    assert po.oversight_blocks_resume_skip("scenes", "auto_images") is True
    assert po.oversight_blocks_resume_skip("scenes", "auto_characters") is True
    assert po.oversight_blocks_resume_skip(None, "scenes") is False


def test_effective_resume_skip() -> None:
    assert po.effective_resume_skip(True, "scenes", "director", True) is True
    assert po.effective_resume_skip(True, "scenes", "scenes", True) is False
    assert po.effective_resume_skip(False, "scenes", "scenes", True) is False


def test_tail_steps_order_characters_before_images() -> None:
    assert po.TAIL_STEPS.index("auto_characters") < po.TAIL_STEPS.index("auto_images")
    assert po.tail_step_index("auto_characters") == 0


def test_tail_should_run() -> None:
    assert po.tail_should_run("auto_characters", None) is True
    assert po.tail_should_run("auto_characters", "auto_characters") is True
    assert po.tail_should_run("auto_characters", "auto_images") is False
    assert po.tail_should_run("auto_images", None) is True
    assert po.tail_should_run("auto_images", "auto_images") is True
    assert po.tail_should_run("auto_images", "auto_narration") is False
    assert po.tail_should_run("auto_narration", "auto_narration") is True


def test_normalize_tail_resume_skips_video_when_disabled() -> None:
    assert po.normalize_tail_resume("auto_videos", auto_scene_videos=False) == "auto_narration"
    assert po.normalize_tail_resume("auto_videos", auto_scene_videos=True) == "auto_videos"


def test_tail_resume_from_oversight_includes_auto_characters() -> None:
    assert po.tail_resume_from_oversight("auto_characters") == "auto_characters"
    assert po.tail_resume_from_oversight("auto_images") == "auto_images"


def test_earliest_gap_deterministic_auto_characters_when_bible_empty() -> None:
    """After scenes are planned, missing ProjectCharacter rows → resume at auto_characters."""
    proj = MagicMock()
    proj.id = uuid4()
    proj.director_output_json = {"brief": "ok"}
    proj.workflow_phase = "scenes_planned"
    db = MagicMock()
    db.scalar.return_value = 0
    with patch(
        "director_api.services.pipeline_oversight.agent_resume_svc.all_scripted_chapters_have_scenes",
        return_value=True,
    ):
        assert po.earliest_gap_deterministic(db, proj, None) == "auto_characters"


def test_earliest_gap_deterministic_no_auto_characters_when_bible_populated() -> None:
    proj = MagicMock()
    proj.id = uuid4()
    proj.director_output_json = {"brief": "ok"}
    proj.workflow_phase = "scenes_planned"
    db = MagicMock()
    db.scalar.return_value = 2
    with patch(
        "director_api.services.pipeline_oversight.agent_resume_svc.all_scripted_chapters_have_scenes",
        return_value=True,
    ):
        assert po.earliest_gap_deterministic(db, proj, None) is None
