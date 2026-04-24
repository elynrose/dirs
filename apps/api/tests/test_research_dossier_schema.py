"""research-dossier/v1 JSON Schema validation."""

import pytest

from director_api.validation.phase2_schemas import validate_research_dossier_body


def _minimal_dossier(**timeline_item_overrides: object) -> dict:
    return {
        "schema_id": "research-dossier/v1",
        "summary": "Test summary for validation.",
        "timeline": [{"label": "Event one", **timeline_item_overrides}],
        "sources_min_met": True,
        "disputed_claims_flagged": False,
    }


def test_approx_year_accepts_integer() -> None:
    validate_research_dossier_body(_minimal_dossier(approx_year=1925))


def test_approx_year_accepts_null() -> None:
    validate_research_dossier_body(_minimal_dossier(approx_year=None))


def test_approx_year_accepts_decade_string() -> None:
    validate_research_dossier_body(_minimal_dossier(approx_year="1920s"))


def test_approx_year_rejects_object() -> None:
    with pytest.raises(Exception):
        validate_research_dossier_body(_minimal_dossier(approx_year={"era": "1920s"}))
