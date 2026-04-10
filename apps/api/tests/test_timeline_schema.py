import jsonschema
import pytest

from director_api.validation.timeline_schema import validate_timeline_document


def test_timeline_minimal_valid():
    validate_timeline_document({"schema_version": 1, "clips": []})


def test_timeline_clip_crossfade_sec_optional():
    validate_timeline_document(
        {"schema_version": 1, "clips": [], "clip_crossfade_sec": 0.75},
    )


def test_timeline_clip_asset_valid():
    validate_timeline_document(
        {
            "schema_version": 1,
            "clips": [
                {
                    "order_index": 0,
                    "source": {"kind": "asset", "asset_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
                }
            ],
        }
    )


def test_timeline_rejects_wrong_schema_version():
    with pytest.raises(jsonschema.ValidationError):
        validate_timeline_document({"schema_version": 3, "clips": []})


def test_timeline_v2_overlays_optional():
    validate_timeline_document(
        {
            "schema_version": 2,
            "clips": [],
            "cut_kind": "fine",
            "overlays": [
                {
                    "type": "title_card",
                    "start_sec": 0,
                    "end_sec": 2,
                    "text": "Episode 1",
                },
                {
                    "type": "lower_third",
                    "start_sec": 1,
                    "end_sec": 5,
                    "text": "Host Name",
                    "subtext": "Location",
                },
                {"type": "map_placeholder", "start_sec": 0, "end_sec": 3, "label": "Route A"},
            ],
        }
    )


def test_timeline_rejects_bad_source():
    with pytest.raises(jsonschema.ValidationError):
        validate_timeline_document(
            {
                "schema_version": 1,
                "clips": [{"order_index": 0, "source": {"kind": "nope", "asset_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}}],
            }
        )
