from unittest.mock import MagicMock

from director_api.services.agent_resume import (
    agent_scenes_chapter_planning_action,
    should_skip_agent_replan_chapter_scenes,
    should_skip_scenes_plan,
)

LONG_SCRIPT = "twelve chars+"
LONG_VO = " ".join(["word"] * 20)


def test_replan_skip_only_when_continue_and_existing_scenes():
    assert should_skip_agent_replan_chapter_scenes(True, False, 2) is True
    assert should_skip_agent_replan_chapter_scenes(True, False, 1) is True
    assert should_skip_agent_replan_chapter_scenes(True, False, 0) is False


def test_replan_not_skipped_for_new_run_or_force():
    assert should_skip_agent_replan_chapter_scenes(False, False, 3) is False
    assert should_skip_agent_replan_chapter_scenes(True, True, 3) is False
    assert should_skip_agent_replan_chapter_scenes(False, True, 3) is False


def test_resume_partial_manual_plan_chapter_a_kept_b_planned():
    """continue_from_existing: chapter A already has scenes is skipped; B with no scenes is planned."""
    assert agent_scenes_chapter_planning_action(LONG_SCRIPT, True, False, 3) == "skip_existing_scenes"
    assert agent_scenes_chapter_planning_action(LONG_SCRIPT, True, False, 0) == "plan"


def test_agent_scenes_chapter_planning_action_matrix():
    assert agent_scenes_chapter_planning_action("short", True, False, 0) == "short_script"
    assert agent_scenes_chapter_planning_action("", True, False, 0) == "short_script"
    assert agent_scenes_chapter_planning_action(LONG_SCRIPT, False, False, 5) == "plan"
    assert agent_scenes_chapter_planning_action(LONG_SCRIPT, True, True, 5) == "plan"


def test_full_video_continue_keeps_existing_scenes_even_one_scene():
    script = f"{LONG_SCRIPT} {LONG_VO}"
    assert agent_scenes_chapter_planning_action(script, True, False, 1, through="full_video") == "skip_existing_scenes"


def test_should_skip_scenes_plan_full_video_does_not_short_circuit_on_one_scene_per_chapter():
    project = type("P", (), {"workflow_phase": "chapters_ready"})()
    db = MagicMock()
    assert should_skip_scenes_plan(True, project, db, through="full_video", force_replan_scenes=False) is False


def test_should_skip_scenes_plan_full_video_still_skips_when_phase_past_scenes():
    project = type("P", (), {"workflow_phase": "scenes_planned"})()
    db = MagicMock()
    assert should_skip_scenes_plan(True, project, db, through="full_video", force_replan_scenes=False) is True


def test_should_skip_scenes_plan_force_replan_enters_scene_step_even_when_scenes_planned():
    project = type("P", (), {"workflow_phase": "scenes_planned"})()
    db = MagicMock()
    assert should_skip_scenes_plan(True, project, db, through="full_video", force_replan_scenes=True) is False
