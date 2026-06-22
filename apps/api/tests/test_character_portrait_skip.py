"""Portrait/chalkboard-only character mentions should not inject the full bible."""

from types import SimpleNamespace

from director_api.services.character_prompt import (
    character_appears_physically_on_screen,
    name_appears_physically_on_screen,
)


def _row(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        match_keys=[name.lower()],
    )


def test_portrait_on_chalkboard_skips_character_bible():
    scene = (
        "In classrooms, people asked whether Victoria shaped her age. "
        "The teacher points at the board."
    )
    prompt = (
        "Subject: Victorian classroom, teacher pointing to a chalkboard showing "
        "Queen Victoria's portrait, students in period dress.\n\n"
        "Composition: wide elevated bird's-eye view"
    )
    row = _row("Queen Victoria")
    assert character_appears_physically_on_screen(row, scene, prompt) is False


def test_physical_presence_includes_character_bible():
    scene = "Queen Victoria addresses a crowded parliament chamber."
    prompt = "Subject: Queen Victoria standing at a podium, parliament in session."
    row = _row("Queen Victoria")
    assert character_appears_physically_on_screen(row, scene, prompt) is True


def test_mixed_mention_includes_when_any_physical():
    scene = "The class studied Queen Victoria's portrait before her arrival."
    prompt = "Subject: Queen Victoria entering the classroom doorway in person."
    row = _row("Queen Victoria")
    assert character_appears_physically_on_screen(row, scene, prompt) is True


def test_name_helper_matches_row_helper():
    scene = "Teacher at chalkboard"
    prompt = "Subject: chalkboard portrait of Queen Victoria"
    row = _row("Queen Victoria")
    assert name_appears_physically_on_screen("Queen Victoria", ["victoria"], scene, prompt) is False
    assert character_appears_physically_on_screen(row, scene, prompt) is False
