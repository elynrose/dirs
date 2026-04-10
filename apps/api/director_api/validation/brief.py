import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_NAME = "documentary_brief.schema.json"


@lru_cache
def _load_schema() -> dict[str, Any]:
    p = Path(__file__).resolve().parent.parent / "schemas" / _SCHEMA_NAME
    return json.loads(p.read_text(encoding="utf-8"))


def validate_documentary_brief(instance: dict[str, Any]) -> None:
    jsonschema.validate(instance=instance, schema=_load_schema())
