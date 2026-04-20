"""Queue a text-to-video ComfyUI workflow (WAN / API-format JSON) and save the output file.

Requires a **running** ComfyUI and a workflow exported with **Save (API Format)** from the graph
that includes your text-to-video WAN nodes (e.g. ``text_to_video_wan``).

Run from ``apps/api`` (uses project venv / ``PYTHONPATH``):

  COMFYUI_VIDEO_WORKFLOW_JSON_PATH=/path/to/text_to_video_wan_api.json \\
  ./scripts/smoke_comfyui_text_to_video.py

Or pass the file explicitly:

  ./scripts/smoke_comfyui_text_to_video.py --workflow /path/to/text_to_video_wan_api.json \\
    --base-url http://127.0.0.1:8188 --out /tmp/smoke_t2v.mp4

``COMFYUI_VIDEO_USE_SCENE_IMAGE`` is forced to ``false`` for this script (pure text-to-video).
Set ``COMFYUI_VIDEO_PROMPT_NODE_ID`` / ``COMFYUI_VIDEO_PROMPT_INPUT_KEY`` if auto CLIP detection
does not match your graph.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    api_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(api_root))

    parser = argparse.ArgumentParser(description="Smoke: ComfyUI text-to-video via Director adapter.")
    parser.add_argument(
        "--workflow",
        type=Path,
        default=None,
        help="API-format workflow JSON. If omitted, uses COMFYUI_VIDEO_WORKFLOW_JSON_PATH.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188").strip(),
        help="ComfyUI HTTP root (default: env COMFYUI_BASE_URL or http://127.0.0.1:8188).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("comfy_smoke_t2v_out.mp4"),
        help="Where to write the downloaded video bytes on success.",
    )
    parser.add_argument(
        "--prompt",
        default="smoke test, soft abstract gradients, no people, no text, short clip",
        help="Positive prompt injected into the workflow.",
    )
    args = parser.parse_args()

    wf = args.workflow
    if wf is None:
        raw = (os.environ.get("COMFYUI_VIDEO_WORKFLOW_JSON_PATH") or "").strip()
        if not raw:
            print(
                "ERROR: pass --workflow /path/to/text_to_video_wan_api.json "
                "or set COMFYUI_VIDEO_WORKFLOW_JSON_PATH.",
                file=sys.stderr,
            )
            return 1
        wf = Path(raw)
    wf = wf.expanduser().resolve()
    if not wf.is_file():
        print(f"ERROR: workflow file not found: {wf}", file=sys.stderr)
        return 1

    os.environ["COMFYUI_BASE_URL"] = args.base_url.rstrip("/")
    os.environ["COMFYUI_VIDEO_WORKFLOW_JSON_PATH"] = str(wf)
    os.environ["COMFYUI_API_FLAVOR"] = "oss"
    os.environ["COMFYUI_VIDEO_USE_SCENE_IMAGE"] = "false"

    import httpx

    from director_api.config import Settings
    from director_api.providers.media_comfyui import generate_scene_video_comfyui

    base = os.environ["COMFYUI_BASE_URL"]
    print("--- ComfyUI reachability ---")
    print(f"  GET {base}/system_stats")
    try:
        r = httpx.get(f"{base}/system_stats", timeout=5.0)
        print(f"  status: {r.status_code}")
        if r.status_code >= 400:
            print(f"  body (truncated): {r.text[:300]!r}")
            return 1
    except httpx.RequestError as e:
        print(f"  ERROR: {e}")
        print("  Is ComfyUI running on this machine? Start it, then re-run this script.")
        return 1

    print("--- generate_scene_video_comfyui (text-to-video) ---")
    print(f"  workflow: {wf}")
    s = Settings()
    res = generate_scene_video_comfyui(
        s,
        args.prompt,
        scene_image_path=None,
        duration_sec=None,
    )
    for k, v in res.items():
        if k == "bytes":
            ln = len(v) if isinstance(v, (bytes, bytearray)) else 0
            print(f"  {k}: <{ln} bytes>")
        else:
            print(f"  {k}: {v}")

    if not res.get("ok"):
        print("RESULT: FAIL")
        return 1

    b = res.get("bytes")
    if not isinstance(b, (bytes, bytearray)) or len(b) < 32:
        print("RESULT: FAIL (empty or tiny payload)")
        return 1

    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(b))
    print(f"RESULT: OK — wrote {out} ({len(b)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
