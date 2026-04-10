import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

# director_api/validation/phase2_schemas.py -> director_api -> apps/api -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


@lru_cache
def _director_schema() -> dict[str, Any]:
    p = _REPO_ROOT / "packages" / "schemas" / "json" / "director-pack.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache
def _research_schema() -> dict[str, Any]:
    p = _REPO_ROOT / "packages" / "schemas" / "json" / "research-dossier.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


def validate_director_pack(instance: dict[str, Any]) -> None:
    jsonschema.validate(instance=instance, schema=_director_schema())


def validate_research_dossier_body(instance: dict[str, Any]) -> None:
    jsonschema.validate(instance=instance, schema=_research_schema())


@lru_cache
def _outline_batch_schema() -> dict[str, Any]:
    p = _REPO_ROOT / "packages" / "schemas" / "json" / "chapter-outline-batch.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache
def _scripts_batch_schema() -> dict[str, Any]:
    p = _REPO_ROOT / "packages" / "schemas" / "json" / "chapter-scripts-batch.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


def validate_chapter_outline_batch(instance: dict[str, Any]) -> None:
    jsonschema.validate(instance=instance, schema=_outline_batch_schema())


def validate_chapter_scripts_batch(instance: dict[str, Any]) -> None:
    jsonschema.validate(instance=instance, schema=_scripts_batch_schema())
