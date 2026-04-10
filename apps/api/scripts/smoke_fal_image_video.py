"""One-off smoke: FAL image + video adapters (requires FAL_KEY). Run from repo: apps/api."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python scripts/smoke_fal_image_video.py` from apps/api
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from director_api.config import get_settings
from director_api.providers.media_fal import generate_scene_image, generate_scene_video_fal


def main() -> int:
    s = get_settings()
    if not (s.fal_key or "").strip():
        print("SKIP: FAL_KEY not set in environment/.env")
        return 2

    print("--- FAL image (generate_scene_image) ---")
    img = generate_scene_image(s, "smoke test, abstract gradient, no text, tiny detail")
    for k, v in img.items():
        if k == "bytes":
            print("  bytes_len:", len(v) if v else 0)
        else:
            print(f"  {k}: {v}")
    if not (img.get("ok") and img.get("bytes") and len(img["bytes"]) >= 32):
        print("RESULT: IMAGE FAIL")
        return 1
    print("RESULT: IMAGE OK")

    print("--- FAL video (generate_scene_video_fal, duration=1) ---")
    vid = generate_scene_video_fal(s, "smoke test, subtle abstract motion, no text", 1.0, model=None)
    for k, v in vid.items():
        if k == "bytes":
            print("  bytes_len:", len(v) if v else 0)
        else:
            print(f"  {k}: {v}")
    if not (vid.get("ok") and vid.get("bytes") and len(vid["bytes"]) >= 100):
        print("RESULT: VIDEO FAIL")
        return 1
    print("RESULT: VIDEO OK")
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
