"""Character bible: normalize LLM quirks before JSON Schema (extra keys, stringy numbers)."""

from director_api.validation.character_schema import (
    normalize_character_bible_llm_output,
    validate_character_bible_batch,
)


def test_extra_keys_stripped_and_validates():
    raw = {
        "schema_id": "character-bible/v1",
        "characters": [
            {
                "sort_order": "0",
                "name": "Ada",
                "role_in_story": "Lead",
                "visual_description": "Tall, dark coat",
                "notes_from_model": "should be ignored",
            }
        ],
    }
    out = validate_character_bible_batch(raw)
    assert out["characters"][0]["name"] == "Ada"
    assert "notes_from_model" not in out["characters"][0]


def test_sort_order_string_and_ordering():
    raw = {
        "schema_id": "character-bible/v1",
        "characters": [
            {"sort_order": 2, "name": "B", "role_in_story": "x", "visual_description": "y"},
            {"sort_order": "1", "name": "A", "role_in_story": "x", "visual_description": "y"},
        ],
    }
    out = normalize_character_bible_llm_output(raw)
    assert [c["name"] for c in out["characters"]] == ["A", "B"]


def test_visual_truncated_to_schema_max():
    long_vis = "x" * 5000
    raw = {
        "schema_id": "character-bible/v1",
        "characters": [
            {
                "sort_order": 0,
                "name": "N",
                "role_in_story": "R",
                "visual_description": long_vis,
            }
        ],
    }
    out = validate_character_bible_batch(raw)
    assert len(out["characters"][0]["visual_description"]) == 4000
