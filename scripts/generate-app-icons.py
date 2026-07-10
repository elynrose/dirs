#!/usr/bin/env python3
"""Generate Electron + web icons from a master logo PNG."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO / "apps" / "electron" / "build-assets" / "icon-source-1024.png"
BUILD_ASSETS = REPO / "apps" / "electron" / "build-assets"
WEB_LOGO = REPO / "apps" / "web" / "public" / "images" / "directely-logo.png"
WEB_FAVICON = REPO / "apps" / "web" / "public" / "favicon.png"


def _center_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def generate(src: Path) -> None:
    if not src.is_file():
        raise SystemExit(f"Source image not found: {src}")
    BUILD_ASSETS.mkdir(parents=True, exist_ok=True)
    WEB_LOGO.parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(src).convert("RGBA")
    square = _center_square(img)
    master = square.resize((1024, 1024), Image.Resampling.LANCZOS)
    master.save(BUILD_ASSETS / "icon-source-1024.png")
    master.resize((512, 512), Image.Resampling.LANCZOS).save(BUILD_ASSETS / "icon.png")
    img.save(WEB_LOGO)
    master.resize((32, 32), Image.Resampling.LANCZOS).save(WEB_FAVICON)

    sizes = [256, 128, 64, 48, 32, 16]
    ico_images = [master.resize((s, s), Image.Resampling.LANCZOS) for s in sizes]
    ico_images[0].save(
        BUILD_ASSETS / "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"Wrote {BUILD_ASSETS / 'icon.ico'}")
    print(f"Wrote {BUILD_ASSETS / 'icon.png'}")
    print(f"Wrote {WEB_LOGO}")
    print(f"Wrote {WEB_FAVICON}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Directely app icons")
    parser.add_argument(
        "source",
        nargs="?",
        default=str(DEFAULT_SRC),
        help="Master logo PNG (default: build-assets/icon-source-1024.png)",
    )
    args = parser.parse_args()
    generate(Path(args.source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
