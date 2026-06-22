"""Character bible injection respects portrait-only mentions."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from director_api.services.image_prompt_assembly import (
    assemble_scene_still_image_prompt,
    character_consistency_block_for_image,
)

LABELED = (
    "Subject: Victorian classroom, teacher pointing to a chalkboard showing "
    "Queen Victoria's portrait, students in period dress.\n\n"
    "Visual treatment: Photoreal cinematic historical epic.\n\n"
    "Composition: wide elevated bird's-eye view"
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


def test_character_block_skips_portrait_only_mention():
    db = MagicMock()
    db.scalars.return_value.all.return_value = [_victoria_row()]
    scene_text = "They debated Victoria's legacy in the classroom."
    prefix = character_consistency_block_for_image(
        db,
        uuid.uuid4(),
        scene_text=scene_text,
        base_prompt=LABELED,
        max_chars=2000,
    )
    assert prefix == ""


def test_character_block_includes_physical_mention():
    db = MagicMock()
    db.scalars.return_value.all.return_value = [_victoria_row()]
    scene_text = "Queen Victoria stood before parliament."
    prompt = "Subject: Queen Victoria standing at a podium in parliament."
    prefix = character_consistency_block_for_image(
        db,
        uuid.uuid4(),
        scene_text=scene_text,
        base_prompt=prompt,
        max_chars=2000,
    )
    assert "Queen Victoria" in prefix
    assert "Petite build" in prefix


def test_assemble_does_not_inject_victoria_for_chalkboard_scene():
    scene = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        order_index=0,
        purpose="Debate legacy",
        narration_text="Did Victoria shape her age?",
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
    db.scalars.return_value.all.return_value = [_victoria_row()]
    db.get.return_value = None

    out = assemble_scene_still_image_prompt(db, scene, project, settings, LABELED)
    assert "Petite build" not in out
    assert "CHARACTER CONSISTENCY" not in out.upper()
