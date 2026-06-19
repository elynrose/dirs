#!/usr/bin/env python3
"""Audit ComfyUI workflow configuration for this workspace."""
from __future__ import annotations

import json
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from director_api.config import get_settings
from director_api.db.session import SessionLocal
from director_api.providers.media_comfyui import (
    _resolve_video_workflow_path,
    _resolve_workflow_path,
    _workflow_env_report_from_path,
    smoke_image,
)
from director_api.services.runtime_settings import get_or_create_app_settings, resolve_runtime_settings


def main() -> int:
    s = get_settings()
    with SessionLocal() as db:
        rt = resolve_runtime_settings(db, s, s.default_tenant_id)
        cfg = get_or_create_app_settings(db, s.default_tenant_id).config_json or {}

    ip = _resolve_workflow_path(rt)
    vp = _resolve_video_workflow_path(rt)
    active = json.loads(vp.read_text(encoding="utf-8"))
    repo_wan = Path("d:/Directely/data/comfyui_workflows/wan_t2v_api.json")
    repo_wf = json.loads(repo_wan.read_text(encoding="utf-8")) if repo_wan.is_file() else {}

    img_env = _workflow_env_report_from_path(
        "image",
        ip,
        prompt_node_id=rt.comfyui_prompt_node_id or "",
        prompt_field="text",
        negative_node_id="",
        load_image_node_id="",
        require_load_image=False,
    )
    vid_env = _workflow_env_report_from_path(
        "video",
        vp,
        prompt_node_id=rt.comfyui_video_prompt_node_id or "",
        prompt_field=rt.comfyui_video_prompt_input_key or "text",
        negative_node_id=rt.comfyui_video_negative_node_id or "",
        load_image_node_id=rt.comfyui_video_load_image_node_id or "",
        require_load_image=bool(rt.comfyui_video_use_scene_image),
    )
    smoke = smoke_image(rt)

    print("=== PATHS ===")
    print("ComfyUI base URL:", rt.comfyui_base_url)
    print(".env image path:", s.comfyui_workflow_json_path)
    print(".env video path:", s.comfyui_video_workflow_json_path)
    print("DB image override:", cfg.get("comfyui_workflow_json_path"))
    print("DB video override:", cfg.get("comfyui_video_workflow_json_path"))
    print("Resolved image:", ip)
    print("Resolved video:", vp)
    print("Same as repo wan_t2v_api.json?", vp.resolve() == repo_wan.resolve())

    print("\n=== NODE IDS (settings vs workflow) ===")
    print("Image prompt node:", rt.comfyui_prompt_node_id, "->", "OK" if rt.comfyui_prompt_node_id in json.loads(ip.read_text(encoding="utf-8")) else "MISSING")
    print("Video prompt node:", rt.comfyui_video_prompt_node_id, "field:", rt.comfyui_video_prompt_input_key)
    print("Video negative node:", rt.comfyui_video_negative_node_id)
    print("Video use scene image:", rt.comfyui_video_use_scene_image, "(LoadImage node:", rt.comfyui_video_load_image_node_id or "none", ")")

    print("\n=== VIDEO WORKFLOW NODES ===")
    for nid in ("6", "7", "37", "38", "39", "40", "49", "50"):
        n = active.get(nid, {})
        print(f"  {nid}: {n.get('class_type')} | {list((n.get('inputs') or {}).keys())}")

    print("\n=== VALIDATION ===")
    print("Image env OK:", img_env["ok"], img_env["errors"])
    print("Video env OK:", vid_env["ok"], vid_env["errors"])
    print("Smoke configured:", smoke.get("configured"))
    print("Smoke workflow_env_ok:", smoke.get("workflow_env_ok"))

    print("\n=== LATENT (node 40) ===")
    print("Active file:", active.get("40", {}).get("inputs"))
    print("Repo wan_t2v_api:", repo_wf.get("40", {}).get("inputs"))
    print("Note: Directely overrides width/height to project aspect ratio and length to clip duration at runtime.")

    test_mp4 = Path(s.local_storage_root) / "comfyui_workflows/00000000-0000-0000-0000-000000000001/test_output.mp4"
    if test_mp4.is_file():
        print("\n=== TEST ARTIFACT ===")
        print("test_output.mp4 size:", test_mp4.stat().st_size, "bytes", "(corrupt if tiny)")

    alt = Path("d:/Directely/data/comfyui_workflows/video_wan2_2_14B_t2v.json")
    if alt.is_file() and alt.resolve() != vp.resolve():
        print("\n=== UNUSED ALTERNATIVE ===")
        print("Found", alt.name, "- different node IDs (e.g. 71/72 not 6/7). Not active; would need re-upload + new node settings.")

    return 0 if img_env["ok"] and vid_env["ok"] and smoke.get("workflow_env_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
