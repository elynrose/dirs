"""JSON Schema validation for Phase 5 timeline documents."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

# director_api/validation/timeline_schema.py -> director_api -> apps/api -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


@lru_cache
def _timeline_version_schema() -> dict[str, Any]:
    p = _REPO_ROOT / "packages" / "schemas" / "json" / "timeline-version.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


def validate_timeline_document(instance: dict[str, Any]) -> None:
    jsonschema.validate(instance=instance, schema=_timeline_version_schema())
