"""Scene plan JSON Schema coercion (LLM extras / order_index quirks)."""

import jsonschema
import pytest

from director_api.services import phase3 as phase3_svc
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


def test_stock_search_terms_optional_and_coerced():
    batch = {
        "schema_id": "scene-plan-batch/v1",
        "scenes": [
            {
                "order_index": 0,
                "purpose": "Beat one",
                "planned_duration_sec": 10,
                "narration_text": "Enough narration for the schema min length here.",
                "visual_type": "b_roll",
                "prompt_package_json": {"image_prompt": "A hill at dusk."},
                "continuity_tags_json": ["a"],
                "stock_search_terms": ["  rolling hills ", "farmer", ""],
            },
        ],
    }
    validate_scene_plan_batch(batch)
    assert batch["scenes"][0]["stock_search_terms"] == ["rolling hills", "farmer"]


def test_stock_search_terms_empty_list_dropped_by_coerce():
    batch = {
        "schema_id": "scene-plan-batch/v1",
        "scenes": [
            {
                "order_index": 0,
                "purpose": "Beat",
                "planned_duration_sec": 10,
                "narration_text": "Enough narration for the schema min length here.",
                "visual_type": "b_roll",
                "prompt_package_json": {"image_prompt": "x"},
                "continuity_tags_json": [],
                "stock_search_terms": ["", "   "],
            },
        ],
    }
    validate_scene_plan_batch(batch)
    assert "stock_search_terms" not in batch["scenes"][0]


def test_merge_stock_search_terms_into_prompt_package():
    item = {"stock_search_terms": ["boats", "dawn"]}
    pp: dict = {"image_prompt": "x"}
    phase3_svc.merge_stock_search_terms_from_plan_row(item, pp)
    assert pp["stock_search_terms"] == ["boats", "dawn"]


def test_infer_stock_search_terms_from_beat():
    terms = phase3_svc.infer_stock_search_terms_for_scene(
        purpose="Quiet harbor at dawn",
        narration_text="The fishing boats leave before first light.",
        chapter_title="Harbor life",
        project_topic="Small ports documentary",
    )
    assert len(terms) >= 2
    assert all(isinstance(x, str) and len(x) <= 80 for x in terms)
