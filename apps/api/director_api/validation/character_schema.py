import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


@lru_cache
def _character_bible_schema() -> dict[str, Any]:
    p = _REPO_ROOT / "packages" / "schemas" / "json" / "character-bible.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


def validate_character_bible_batch(instance: dict[str, Any]) -> None:
    jsonschema.validate(instance=instance, schema=_character_bible_schema())
