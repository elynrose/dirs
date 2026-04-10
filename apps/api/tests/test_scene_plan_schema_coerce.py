"""Scene plan JSON Schema coercion (LLM extras / order_index quirks)."""

import jsonschema
import pytest

from director_api.validation.phase3_schemas import coerce_scene_plan_batch, validate_scene_plan_batch


def test_validate_coerces_extra_keys_reindexes_and_clamps_duration():
    batch = {
        "schema_id": "scene-plan-batch/v1",
        "scenes": [
            {
                "order_index": 1,
                "purpose": "Open",
                "planned_duration_sec": 2,
                "narration_text": "First beat narration text here.",
                "visual_type": "b_roll",
                "prompt_package_json": {"image_prompt": "A forest at dawn."},
                "continuity_tags_json": ["loc_a"],
                "llm_notes": "should be removed",
            },
            {
                "order_index": 3,
                "purpose": "Close",
                "planned_duration_sec": 90,
                "narration_text": "Second beat narration text here.",
                "visual_type": "b_roll",
                "prompt_package_json": {"image_prompt": "River reflection."},
                "continuity_tags_json": ["loc_b"],
            },
        ],
    }
    validate_scene_plan_batch(batch)
    assert batch["scenes"][0]["order_index"] == 0
    assert batch["scenes"][1]["order_index"] == 1
    assert "llm_notes" not in batch["scenes"][0]
    assert batch["scenes"][0]["planned_duration_sec"] == 3


def test_coerce_drops_non_object_prompt_package():
    batch = {
        "schema_id": "scene-plan-batch/v1",
        "scenes": [
            {
                "order_index": 0,
                "purpose": "x",
                "planned_duration_sec": 10,
                "narration_text": "Enough narration for the schema min length.",
                "visual_type": "b_roll",
                "prompt_package_json": "not an object",
                "continuity_tags_json": [],
            },
        ],
    }
    coerce_scene_plan_batch(batch)
    assert batch["scenes"][0]["prompt_package_json"] == {}


def test_validate_rejects_empty_scenes_after_coerce():
    with pytest.raises(jsonschema.ValidationError):
        validate_scene_plan_batch({"schema_id": "scene-plan-batch/v1", "scenes": []})
