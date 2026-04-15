"""Hard prerequisite floors for full-video tail resume (oversight cannot skip past missing work)."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from director_api.services.pipeline_oversight import (
    clamp_tail_resume_to_hard_floor,
    compute_hard_tail_floor,
)


def test_clamp_none_tail_full_run_ok_with_any_floor():
    assert clamp_tail_resume_to_hard_floor(None, "auto_characters") is None
    assert clamp_tail_resume_to_hard_floor(None, "auto_images") is None


def test_clamp_no_change_when_suggested_before_floor():
    assert clamp_tail_resume_to_hard_floor("auto_characters", "auto_images") == "auto_characters"
    assert clamp_tail_resume_to_hard_floor("auto_images", "auto_images") == "auto_images"


def test_clamp_pulls_back_when_suggested_after_floor():
    assert clamp_tail_resume_to_hard_floor("auto_narration", "auto_images") == "auto_images"
    assert clamp_tail_resume_to_hard_floor("auto_final_cut", "auto_characters") == "auto_characters"


@pytest.fixture
def mock_db_for_tail_floor():
    """Scalar returns ProjectCharacter count; scalars().all() returns scene ids that have succeeded visuals."""

    def _make(*, character_count: int, visual_scene_ids: list):
        db = MagicMock()
        db.scalar.return_value = character_count
        m = MagicMock()
        m.all.return_value = visual_scene_ids
        db.scalars.return_value = m
        return db

    return _make


def test_compute_floor_characters_when_no_rows(mock_db_for_tail_floor):
    pid = uuid4()
    db = mock_db_for_tail_floor(character_count=0, visual_scene_ids=[])
    assert compute_hard_tail_floor(db, pid, []) == "auto_characters"


def test_compute_floor_images_when_chars_but_missing_visuals(mock_db_for_tail_floor):
    pid = uuid4()
    s1, s2 = uuid4(), uuid4()
    db = mock_db_for_tail_floor(character_count=2, visual_scene_ids=[s1])
    assert compute_hard_tail_floor(db, pid, [s1, s2]) == "auto_images"


def test_compute_floor_none_when_all_have_visuals(mock_db_for_tail_floor):
    pid = uuid4()
    s1 = uuid4()
    db = mock_db_for_tail_floor(character_count=1, visual_scene_ids=[s1])
    assert compute_hard_tail_floor(db, pid, [s1]) is None
