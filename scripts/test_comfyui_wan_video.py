#!/usr/bin/env python3
"""
Run one **ComfyUI WAN / text-to-video** job using the same code path as scene video generation
(`generate_scene_video_comfyui`), driven by **process + repo .env** (same as the API).

Prerequisites
-------------
1. ComfyUI running locally, e.g. ``http://127.0.0.1:8188`` (``COMFYUI_BASE_URL``).
2. In ComfyUI: build your **text_to_video_wan** graph, then **Workflow → Export (API format)**.
3. Save the JSON under the repo, e.g. ``data/comfyui_workflows/text_to_video_wan_api.json``.
4. In repo ``.env`` (or workspace settings in the DB), set at least:

   - ``COMFYUI_VIDEO_WORKFLOW_JSON_PATH`` — path to that API JSON (repo-relative or absolute).
   - ``COMFYUI_VIDEO_USE_SCENE_IMAGE=false`` — for pure text-to-video (no scene still upload).
   - ``COMFYUI_VIDEO_PROMPT_NODE_ID`` — string id of the node whose **inputs** receive the prompt
     (open the JSON; keys are node ids like ``"12"``).
   - ``COMFYUI_VIDEO_PROMPT_INPUT_KEY`` — usually ``text`` for CLIP-style nodes; WAN nodes may use
     ``prompt`` or another key — it must **already exist** on that node in the exported JSON.

   Optional: ``COMFYUI_VIDEO_NEGATIVE_NODE_ID``, ``COMFYUI_VIDEO_DEFAULT_NEGATIVE_PROMPT``.

Usage (Windows, from repo root)::

  cd apps\\api
  .\\.venv-win\\Scripts\\python.exe ..\\..\\scripts\\test_comfyui_wan_video.py

  .\\.venv-win\\Scripts\\python.exe ..\\..\\scripts\\test_comfyui_wan_video.py --prompt "A calm ocean at sunset"

Output is written to ``.run/comfyui_wan_test.mp4`` (or ``--output``).
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Test ComfyUI WAN / text-to-video via Director settings.")
    parser.add_argument(
        "--prompt",
        default="Short test clip: soft morning light over hills, slow pan, cinematic.",
        help="Positive prompt text injected into the workflow.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Write video bytes here (default: .run/comfyui_wan_test.mp4 under repo root).",
    )
    args = parser.parse_args()

    api_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    os.chdir(api_dir)

    from director_api.config import get_settings
    from director_api.providers.media_comfyui import generate_scene_video_comfyui

    s = get_settings()
    base = (s.comfyui_base_url or "").strip()
    wf = (s.comfyui_video_workflow_json_path or "").strip()
    print("COMFYUI_BASE_URL:", base or "(empty)")
    print("COMFYUI_VIDEO_WORKFLOW_JSON_PATH:", wf or "(empty)")
    print("COMFYUI_VIDEO_USE_SCENE_IMAGE:", getattr(s, "comfyui_video_use_scene_image", None))
    print("COMFYUI_VIDEO_PROMPT_NODE_ID:", (getattr(s, "comfyui_video_prompt_node_id", None) or "").strip() or "(auto / empty)")
    print("COMFYUI_VIDEO_PROMPT_INPUT_KEY:", (getattr(s, "comfyui_video_prompt_input_key", None) or "").strip() or "(default text)")

    if not wf:
        print(
            "\nSet COMFYUI_VIDEO_WORKFLOW_JSON_PATH in .env to your exported API JSON, then re-run.",
            file=sys.stderr,
        )
        return 1

    if getattr(s, "comfyui_video_use_scene_image", True):
        print(
            "WARNING: COMFYUI_VIDEO_USE_SCENE_IMAGE=true (default). For text-to-video, set "
            "COMFYUI_VIDEO_USE_SCENE_IMAGE=false in .env or the run will fail without a scene image.\n",
            file=sys.stderr,
        )

    repo_root = os.path.abspath(os.path.join(api_dir, "..", ".."))
    out_path = args.output.strip() or os.path.join(repo_root, ".run", "comfyui_wan_test.mp4")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print("\nSubmitting prompt to ComfyUI (this may take several minutes)…")
    res = generate_scene_video_comfyui(s, args.prompt, scene_image_path=None)
    if not res.get("ok"):
        print("FAILED:", res.get("error"), file=sys.stderr)
        d = res.get("detail")
        if d:
            print(d[:4000], file=sys.stderr)
        return 1

    data = res.get("bytes") or b""
    with open(out_path, "wb") as f:
        f.write(data)
    ct = res.get("content_type") or "video/mp4"
    print(f"OK: wrote {len(data)} bytes to {out_path} ({ct})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
