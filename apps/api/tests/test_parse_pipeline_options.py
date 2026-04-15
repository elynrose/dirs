"""Regression: hands-off (unattended) must not default to critique depth."""

from director_api.services.agent_resume import parse_pipeline_options


def test_unattended_missing_through_is_full_video():
    cont, through, unattended = parse_pipeline_options({"unattended": True, "continue_from_existing": True})
    assert cont is True
    assert unattended is True
    assert through == "full_video"


def test_unattended_with_critique_coerced_to_full_video():
    cont, through, unattended = parse_pipeline_options(
        {"unattended": True, "through": "critique", "continue_from_existing": False}
    )
    assert cont is False
    assert unattended is True
    assert through == "full_video"


def test_unattended_explicit_chapters_preserved():
    _, through, unattended = parse_pipeline_options({"unattended": True, "through": "chapters"})
    assert unattended is True
    assert through == "chapters"


def test_attended_default_critique():
    _, through, unattended = parse_pipeline_options({"continue_from_existing": True})
    assert unattended is False
    assert through == "critique"
