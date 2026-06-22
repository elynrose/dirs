"""Video prompt assembly mirrors still-image portrait skip and labeled pass-through."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from director_api.services.image_prompt_assembly import assemble_scene_video_prompt
from director_api.services.narration_bracket_visual import video_text_prompt_from_scene_fields

LABELED_VIDEO = (
    "Subject: Victorian classroom, teacher pointing to a chalkboard showing "
    "Queen Victoria's portrait, students in period dress.\n\n"
    "Visual treatment: Photoreal cinematic historical epic.\n\n"
    "Composition: wide elevated bird's-eye view\n\n"
    "Motion: slow observational pan across the classroom"
)


def _victoria_row():
    return SimpleNamespace(
        name="Queen Victoria",
        match_keys=["queen victoria", "victoria"],
        role_in_story="The central figure of the documentary",
        visual_description="Petite build, fair skin, round face, blue eyes.",
        time_place_scope_notes="1837 to 1901",
        short_visual_tag="Petite, fair skin, blue eyes",
        sort_order=0,
    )


def test_video_prefers_substantial_package_over_brackets():
    pp = {"video_prompt": LABELED_VIDEO}
    out = video_text_prompt_from_scene_fields(
        narration_text="See [the temple] at dawn.",
        purpose=None,
        visual_type=None,
        prompt_package_json=pp,
        video_prompt_override=None,
    )
    assert "Victorian classroom" in out
    assert "the temple" not in out.lower()


def test_video_falls_back_to_substantial_image_prompt():
    image_only = (
        "Subject: Victorian classroom with chalkboard portrait of Queen Victoria.\n\n"
        "Visual treatment: Photoreal cinematic historical epic.\n\n"
        "Motion: gentle classroom drift"
    )
    pp = {"image_prompt": image_only, "video_prompt": "short"}
    out = video_text_prompt_from_scene_fields(
        narration_text="See [ignored].",
        purpose=None,
        visual_type=None,
        prompt_package_json=pp,
        video_prompt_override=None,
    )
    assert "Victorian classroom" in out
    assert out.strip() != "short"


def test_labeled_video_skips_camera_and_victoria_bible():
    scene = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        order_index=1,
        purpose="Debate legacy",
        narration_text="Did Victoria shape her age?",
        visual_type=None,
        prompt_package_json={"video_prompt": LABELED_VIDEO},
    )
    project = SimpleNamespace(
        id=uuid.uuid4(),
        visual_style="preset:cinematic_documentary",
        topic="Victorian Britain",
        title="After the Mourning",
        include_spoken_dialogue_in_video_prompt=False,
    )
    settings = SimpleNamespace(visual_style_preset="cinematic_documentary")

    db = MagicMock()
    db.scalars.return_value.all.return_value = [_victoria_row()]
    db.get.return_value = None

    out = assemble_scene_video_prompt(db, scene, project, settings, LABELED_VIDEO)
    assert "Victorian classroom" in out
    assert "Motion:" in out
    assert "Camera motion:" not in out
    assert "Camera perspective:" not in out
    assert "CHARACTER CONSISTENCY" not in out.upper()
    assert "Petite build" not in out


def test_preview_video_path_matches_assembly():
    from director_api.tasks.prompt_runtime_helpers import _scene_video_prompt_for_provider

    scene = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        order_index=1,
        purpose="Debate legacy",
        narration_text="Victoria's portrait on the chalkboard.",
        visual_type=None,
        prompt_package_json={"video_prompt": LABELED_VIDEO},
    )
    project = SimpleNamespace(
        id=uuid.uuid4(),
        visual_style="preset:cinematic_documentary",
        topic="Victorian Britain",
        title="After the Mourning",
        include_spoken_dialogue_in_video_prompt=False,
    )
    settings = SimpleNamespace(visual_style_preset="cinematic_documentary")

    db = MagicMock()
    db.scalars.return_value.all.return_value = [_victoria_row()]
    db.get.return_value = None

    preview = _scene_video_prompt_for_provider(db, scene, project, settings)
    assert "Victorian classroom" in preview
    assert "CHARACTER CONSISTENCY" not in preview.upper()
