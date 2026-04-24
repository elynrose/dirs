"""Hard prerequisite floors for full-video tail resume (oversight cannot skip past missing work)."""

from uuid import uuid4

from director_api.services.pipeline_oversight import (
    clamp_tail_resume_to_hard_floor,
    compute_tail_media_floor,
)


def test_clamp_none_tail_full_run_ok_with_any_floor():
    assert clamp_tail_resume_to_hard_floor(None, "auto_characters") is None
    assert clamp_tail_resume_to_hard_floor(None, "auto_images") is None


def test_clamp_no_change_when_suggested_before_floor():
    assert clamp_tail_resume_to_hard_floor("auto_characters", "auto_images") == "auto_characters"
    assert clamp_tail_resume_to_hard_floor("auto_images", "auto_images") == "auto_images"


def test_clamp_pulls_back_when_suggested_after_floor():
    assert clamp_tail_resume_to_hard_floor("auto_timeline", "auto_images") == "auto_images"
    assert clamp_tail_resume_to_hard_floor("auto_final_cut", "auto_characters") == "auto_characters"


def test_compute_tail_media_floor_needs_images():
    s1, s2 = uuid4(), uuid4()
    assert (
        compute_tail_media_floor(
            [s1, s2],
            {s1: 1},
            {},
            auto_generate_scene_images=True,
            auto_generate_scene_videos=False,
            min_scene_images=1,
            min_scene_videos=1,
        )
        == "auto_images"
    )


def test_compute_tail_media_floor_needs_videos_after_images():
    s1, s2 = uuid4(), uuid4()
    assert (
        compute_tail_media_floor(
            [s1, s2],
            {s1: 1, s2: 1},
            {s1: 0},
            auto_generate_scene_images=True,
            auto_generate_scene_videos=True,
            min_scene_images=1,
            min_scene_videos=1,
        )
        == "auto_videos"
    )


def test_compute_tail_media_floor_none_when_satisfied():
    s1 = uuid4()
    assert (
        compute_tail_media_floor(
            [s1],
            {s1: 1},
            {s1: 1},
            auto_generate_scene_images=True,
            auto_generate_scene_videos=True,
            min_scene_images=1,
            min_scene_videos=1,
        )
        is None
    )


def test_compute_tail_media_floor_min_images_gt_one():
    s1 = uuid4()
    assert (
        compute_tail_media_floor(
            [s1],
            {s1: 1},
            {},
            auto_generate_scene_images=True,
            auto_generate_scene_videos=False,
            min_scene_images=2,
            min_scene_videos=1,
        )
        == "auto_images"
    )
