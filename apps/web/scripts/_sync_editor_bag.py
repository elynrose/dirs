#!/usr/bin/env python3
"""Sync studioEditor bag with identifiers used in StudioEditorView.jsx."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/App.jsx"
VIEW = ROOT / "src/components/StudioEditorView.jsx"
KEYS = ROOT / "scripts/_editor_bag_keys.txt"

SKIP = {
    "true", "false", "null", "undefined", "void", "return", "new", "class", "if", "else",
    "for", "while", "do", "try", "catch", "finally", "throw", "typeof", "await", "async",
    "function", "const", "let", "var", "import", "export", "from", "this", "delete", "in", "of",
    "Math", "Number", "String", "Boolean", "Array", "Object", "Date", "JSON", "Intl",
    "document", "window", "navigator", "console", "Promise", "Set", "Map",
}


def app_bindings(app_src: str) -> set[str]:
    m = re.search(r"export default function App\(\) \{", app_src)
    mod, body = app_src[: m.start()], app_src[m.end() :]
    names: set[str] = set()
    for src in (mod, body):
        for mm in re.finditer(r"^function\s+(\w+)\s*\(", src, re.MULTILINE):
            names.add(mm.group(1))
    for line in body.splitlines():
        mm = re.match(r"^\s*const\s+(\w+)\s*=", line)
        if mm:
            names.add(mm.group(1))
        mm = re.match(r"^\s*const\s*\[([^\]]+)\]\s*=", line)
        if mm:
            for p in mm.group(1).split(","):
                names.add(p.strip().split("=")[0].strip())
        mm = re.match(r"^\s*const\s*\{([^}]+)\}\s*=", line)
        if mm:
            for p in mm.group(1).split(","):
                p = p.strip()
                if ":" in p:
                    p = p.split(":")[-1].strip()
                names.add(p.split("=")[0].strip())
    return names


def view_imports(view_src: str) -> set[str]:
    names: set[str] = set()
    for m in re.finditer(r"import\s+(?:\{([^}]+)\}|(\w+))\s+from", view_src):
        if m.group(1):
            for part in m.group(1).split(","):
                part = part.strip()
                if " as " in part:
                    names.add(part.split(" as ")[-1].strip())
                else:
                    names.add(part.split("=")[0].strip())
        elif m.group(2):
            names.add(m.group(2))
    return names


def main() -> None:
    app_src = APP.read_text(encoding="utf-8")
    view_src = VIEW.read_text(encoding="utf-8")
    bindings = app_bindings(app_src)
    imports = view_imports(view_src)

    dest_m = re.search(r"const \{\n([\s\S]*?)\n  \} = useStudioEditor", view_src)
    if not dest_m:
        raise SystemExit("destructure not found")
    bag = {x.strip() for x in dest_m.group(1).split(",") if x.strip()}

    body = view_src[dest_m.end() :]
    tokens = set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)", body))
    used = {t for t in tokens if t not in SKIP and t not in imports}

    missing = sorted(t for t in used if t in bindings and t not in bag)
    print(f"adding {len(missing)} keys:", ", ".join(missing[:30]), "..." if len(missing) > 30 else "")

    all_keys = sorted(bag | set(missing))
    KEYS.write_text("\n".join(all_keys) + "\n", encoding="utf-8")

    dest = ",\n    ".join(all_keys)
    view_src = re.sub(
        r"const \{\n[\s\S]*?\n  \} = useStudioEditor\(\)",
        f"const {{\n    {dest}\n  }} = useStudioEditor()",
        view_src,
        count=1,
    )
    VIEW.write_text(view_src, encoding="utf-8")

    bag_body = "\n".join(f"      {k}," for k in all_keys)
    deps = ",\n      ".join(all_keys)
    memo = f"""  const studioEditorValue = useMemo(
    () => ({{
{bag_body}
    }}),
    [
      {deps},
    ],
  );

"""
    app_src = re.sub(
        r"  const studioEditorValue = useMemo\([\s\S]*?\n  \);\n\n",
        memo,
        app_src,
        count=1,
    )
    APP.write_text(app_src, encoding="utf-8")
    print(f"total bag keys: {len(all_keys)}")


if __name__ == "__main__":
    main()
