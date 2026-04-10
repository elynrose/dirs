import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

_SCENE_PLAN_SCENE_KEYS = frozenset(
    {
        "order_index",
        "purpose",
        "planned_duration_sec",
        "narration_text",
        "visual_type",
        "prompt_package_json",
        "continuity_tags_json",
        "preferred_image_provider",
        "preferred_video_provider",
    }
)


@lru_cache
def _scene_plan_batch_schema() -> dict[str, Any]:
    p = _REPO_ROOT / "packages" / "schemas" / "json" / "scene-plan-batch.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


def coerce_scene_plan_batch(instance: dict[str, Any]) -> None:
    """Mutate a scene-plan dict in place so JSON Schema validation is more likely to succeed.

    LLMs often add extra keys (``additionalProperties: false``) or use 1-based / non-contiguous
    ``order_index`` values; OpenRouter/Gemini may wrap fields we do not accept.
    """
    if not isinstance(instance, dict):
        return
    raw_scenes = instance.get("scenes")
    if not isinstance(raw_scenes, list):
        return
    scenes: list[dict[str, Any]] = []
    for s in raw_scenes:
        if not isinstance(s, dict):
            continue
        row: dict[str, Any] = {}
        for k in _SCENE_PLAN_SCENE_KEYS:
            if k not in s:
                continue
            if k == "prompt_package_json" and not isinstance(s[k], dict):
                continue
            if k == "continuity_tags_json" and not isinstance(s[k], list):
                continue
            row[k] = s[k]
        if "prompt_package_json" not in row:
            row["prompt_package_json"] = {}
        ct = row.get("continuity_tags_json")
        if not isinstance(ct, list):
            row["continuity_tags_json"] = []
        else:
            row["continuity_tags_json"] = [str(x)[:256] for x in ct if x is not None][:32]
        try:
            pd = int(row["planned_duration_sec"])
        except (KeyError, TypeError, ValueError):
            pd = 5
        row["planned_duration_sec"] = max(3, min(600, pd))
        scenes.append(row)
    scenes.sort(key=lambda x: int(x.get("order_index") or 0))
    for i, row in enumerate(scenes):
        row["order_index"] = i
    instance["schema_id"] = "scene-plan-batch/v1"
    instance["scenes"] = scenes


def validate_scene_plan_batch(instance: dict[str, Any]) -> None:
    coerce_scene_plan_batch(instance)
    jsonschema.validate(instance=instance, schema=_scene_plan_batch_schema())
