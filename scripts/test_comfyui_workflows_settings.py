#!/usr/bin/env python3
"""Run ComfyUI connection + image + video tests using workspace settings."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
os.chdir(API_DIR)

from director_api.config import get_settings  # noqa: E402
from director_api.db.session import SessionLocal  # noqa: E402
from director_api.providers.media_comfyui import (  # noqa: E402
    generate_scene_image_comfyui,
    generate_scene_video_comfyui,
    smoke_image,
)
from director_api.services.runtime_settings import resolve_runtime_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    with SessionLocal() as db:
        rt = resolve_runtime_settings(db, settings, settings.default_tenant_id)

    results: dict[str, object] = {}

    t0 = time.monotonic()
    smoke = smoke_image(rt)
    results["connection"] = {
        "ok": bool(smoke.get("configured")),
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "smoke": smoke,
    }
    print("connection:", json.dumps(results["connection"], indent=2)[:1200])

    if not results["connection"]["ok"]:
        print("Connection/smoke failed; skipping generation tests.", file=sys.stderr)
        return 1

    img_prompt = "Directely test — simple documentary still, soft daylight, one subject"
    t0 = time.monotonic()
    img = generate_scene_image_comfyui(rt, img_prompt)
    results["image"] = {
        "ok": bool(img.get("ok")),
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "error": img.get("error"),
        "detail": (img.get("detail") or "")[:500],
        "bytes": len(img.get("bytes") or b""),
    }
    print("image:", json.dumps({k: v for k, v in results["image"].items() if k != "bytes"}, indent=2))

    if not results["image"]["ok"]:
        print(json.dumps(results, indent=2))
        return 1

    vid_prompt = "Directely test — slow pan over quiet hills, cinematic"
    dur = min(5.0, float(getattr(rt, "scene_clip_duration_sec", 5) or 5))
    t0 = time.monotonic()
    vid = generate_scene_video_comfyui(rt, vid_prompt, scene_image_path=None, duration_sec=dur)
    results["video"] = {
        "ok": bool(vid.get("ok")),
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "error": vid.get("error"),
        "detail": (vid.get("detail") or "")[:500],
        "bytes": len(vid.get("bytes") or b""),
        "duration_sec": dur,
    }
    print("video:", json.dumps({k: v for k, v in results["video"].items() if k != "bytes"}, indent=2))

    ok = bool(results["image"]["ok"]) and bool(results["video"]["ok"])
    print("\nALL_OK" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
