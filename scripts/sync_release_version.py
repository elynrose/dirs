"""Bump aligned version in pyproject.toml + apps/web + apps/electron package.json."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: sync_release_version.py <semver> <repo_root>", file=sys.stderr)
        raise SystemExit(2)
    ver, root_s = sys.argv[1], sys.argv[2]
    root = Path(root_s).resolve()
    toml_path = root / "apps" / "api" / "pyproject.toml"
    text = toml_path.read_text(encoding="utf-8")
    text2, n = re.subn(r'(?m)^version\s*=\s*"[^"]*"', f'version = "{ver}"', text, count=1)
    if n != 1:
        raise SystemExit(f"{toml_path}: expected 1 version line, got {n}")
    toml_path.write_text(text2, encoding="utf-8", newline="\n")
    for rel in ("apps/web/package.json", "apps/electron/package.json"):
        p = root / rel
        raw = p.read_text(encoding="utf-8").lstrip("\ufeff").lstrip()
        if not raw.startswith("{"):
            raw = "{" + raw
        data = json.loads(raw)
        data["version"] = ver
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Set version {ver} in pyproject.toml + web + electron package.json")


if __name__ == "__main__":
    main()
