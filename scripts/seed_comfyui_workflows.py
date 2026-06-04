#!/usr/bin/env python3
"""Copy flux_dev (image) and wan_t2v_api (video) into workspace ComfyUI settings storage."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
os.chdir(API_DIR)

from director_api.config import get_settings  # noqa: E402
from director_api.db.session import SessionLocal  # noqa: E402
from director_api.services.comfyui_workflow_storage import (  # noqa: E402
    save_workflow_json,
    workflow_config_key,
    workflow_storage_key,
)
from director_api.services.runtime_settings import (  # noqa: E402
    get_or_create_app_settings,
    invalidate_runtime_settings_cache_after_tenant_config_persisted,
    sanitize_overrides,
)

SRC = REPO_ROOT / "data" / "comfyui_workflows"
MAPPING = {
    "image": SRC / "flux_dev.json",
    "video": SRC / "wan_t2v_api.json",
}


def main() -> int:
    for role, path in MAPPING.items():
        if not path.is_file():
            print(f"Missing workflow file: {path}", file=sys.stderr)
            return 1

    settings = get_settings()
    root = Path(settings.local_storage_root).resolve()
    tenant = str(settings.default_tenant_id)

    with SessionLocal() as db:
        row = get_or_create_app_settings(db, tenant)
        prior = dict(row.config_json or {})
        summary: dict[str, object] = {}
        for role, path in MAPPING.items():
            workflow = json.loads(path.read_text(encoding="utf-8"))
            key = save_workflow_json(
                storage_root=root, tenant_id=tenant, role=role, workflow=workflow
            )
            prior[workflow_config_key(role)] = key
            summary[role] = {
                "storage_key": key,
                "node_count": len(workflow),
                "source": str(path),
            }

        prior["comfyui_prompt_node_id"] = "56:51"
        prior.pop("comfyui_negative_node_id", None)  # Flux uses ConditioningZeroOut, not a negative CLIP node
        prior["comfyui_video_prompt_node_id"] = "6"
        prior["comfyui_video_negative_node_id"] = "7"
        prior["comfyui_video_prompt_input_key"] = "text"
        prior["comfyui_video_use_scene_image"] = False
        prior["comfyui_api_flavor"] = "oss"
        prior["comfyui_base_url"] = "http://127.0.0.1:8188"

        # App-wide media defaults (Studio workspace settings)
        prior["active_image_provider"] = "comfyui"
        prior["active_video_provider"] = "comfyui_wan"
        prior["director_placeholder_media"] = False

        row.config_json = sanitize_overrides(prior)
        db.commit()
        invalidate_runtime_settings_cache_after_tenant_config_persisted(get_settings(), tenant)

    print(json.dumps(summary, indent=2))
    print("tenant:", tenant)
    print("storage_root:", root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
