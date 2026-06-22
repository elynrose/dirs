"""Shared still-image prompt assembly for jobs and resolved-prompt preview."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from director_api.services.image_prompt_assembly import assemble_scene_still_image_prompt


LABELED = (
    "Subject: Victorian classroom, teacher pointing to a chalkboard showing "
    "Queen Victoria's portrait, students in period dress.\n\n"
    "Visual treatment: Photoreal cinematic historical epic.\n\n"
    "Composition: wide elevated bird's-eye view"
)


def test_labeled_prompt_skips_camera_injection():
    scene_id = uuid.uuid4()
    scene = SimpleNamespace(
        id=scene_id,
        chapter_id=uuid.uuid4(),
        order_index=0,
        purpose="Debate legacy",
        narration_text="See [ignored bracket].",
        prompt_package_json={"image_prompt": LABELED},
    )
    project = SimpleNamespace(
        id=uuid.uuid4(),
        visual_style="preset:cinematic_documentary",
        topic="Victorian Britain",
        title="After the Mourning",
    )
    settings = SimpleNamespace(visual_style_preset="cinematic_documentary")

    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    db.get.return_value = None

    out = assemble_scene_still_image_prompt(db, scene, project, settings, LABELED)
    assert "Victorian classroom" in out
    assert "bird's-eye view" in out
    assert "Camera perspective:" not in out
    assert "CHARACTER CONSISTENCY" not in out.upper()


def test_preview_path_skips_camera_and_character_bible():
    from director_api.tasks.prompt_runtime_helpers import _scene_still_prompt_for_comfy

    scene_id = uuid.uuid4()
    chapter_id = uuid.uuid4()
    scene = SimpleNamespace(
        id=scene_id,
        chapter_id=chapter_id,
        order_index=2,
        purpose="Debate legacy",
        narration_text="Victoria's portrait hangs above the chalkboard.",
        prompt_package_json={"image_prompt": LABELED},
    )
    project = SimpleNamespace(
        id=uuid.uuid4(),
        visual_style="preset:cinematic_documentary",
        topic="Victorian Britain",
        title="After the Mourning",
    )
    settings = SimpleNamespace(visual_style_preset="cinematic_documentary")

    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    db.get.return_value = SimpleNamespace(title="After the Mourning")

    preview = _scene_still_prompt_for_comfy(db, scene, project, settings)
    assert "Victorian classroom" in preview
    assert "Camera perspective:" not in preview
    assert "CHARACTER CONSISTENCY" not in preview.upper()
