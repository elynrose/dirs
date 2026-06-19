#!/usr/bin/env python3
"""Sync or verify studioEditor bag: StudioEditorView identifiers vs App.jsx bindings."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/App.jsx"
VIEW = ROOT / "src/components/StudioEditorView.jsx"
COMPOSITION = ROOT / "src/hooks/editor/useStudioEditorComposition.js"
KEYS = ROOT / "scripts/_editor_bag_keys.txt"

SKIP = {
    "true", "false", "null", "undefined", "void", "return", "new", "class", "if", "else",
    "for", "while", "do", "try", "catch", "finally", "throw", "typeof", "await", "async",
    "function", "const", "let", "var", "import", "export", "from", "this", "delete", "in", "of",
    "Math", "Number", "String", "Boolean", "Array", "Object", "Date", "JSON", "Intl",
    "document", "window", "navigator", "console", "Promise", "Set", "Map",
}

# Loop-variable false positives (local const in JSX, not App bindings)
FALSE_POSITIVE = {
    "as", "b", "c", "d", "e", "em", "err", "data", "i", "m", "n", "p", "s", "v", "x", "y",
    "row", "col", "ev", "a", "id", "key", "ref", "src", "alt", "type", "style", "class",
    "current", "detail", "diff", "dur", "existing", "extra", "first", "fit", "clip", "fromId",
    "toId", "left", "right", "top", "bottom", "width", "height", "size", "text", "html", "body",
    "j", "idx", "i2v", "t2v", "r", "re", "rk", "ta", "tsrc", "ttype", "tv", "path", "local",
    "lib", "list", "jobs", "hints", "ok", "order", "pid", "prev", "rows", "session", "short",
    "silent", "start", "steps", "sync", "t", "used", "words", "phKind", "placeholderKind",
    "previewKind", "importKey", "isImporting", "isSelected", "mediaJobs", "planned", "readSec",
    "rel", "prompts", "canCopyPrompt", "needsTrimChoice", "sceneActive", "thumbAsset",
    "thumbSrc", "thumbType", "now", "to", "assets", "generationPrompt", "activeAr", "canListStart",
    "label", "meta", "scene",
}


def app_bindings(app_src: str) -> set[str]:
    m = re.search(r"export default function App\(\) \{", app_src)
    if not m:
        raise SystemExit("App component not found")
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
            for part in mm.group(1).split(","):
                names.add(part.strip().split("=")[0].strip())
        mm = re.match(r"^\s*const\s*\{([^}]+)\}\s*=", line)
        if mm:
            for part in mm.group(1).split(","):
                part = part.strip()
                if ":" in part:
                    part = part.split(":")[-1].strip()
                names.add(part.split("=")[0].strip())
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


def bag_destructure_keys(view_src: str) -> set[str]:
    dest_m = re.search(r"const \{\n([\s\S]*?)\n  \} = useStudioEditor", view_src)
    if not dest_m:
        raise SystemExit("useStudioEditor destructure not found in StudioEditorView.jsx")
    return {x.strip() for x in dest_m.group(1).split(",") if x.strip()}


def memo_keys(app_src: str) -> set[str]:
    comp_src = COMPOSITION.read_text(encoding="utf-8") if COMPOSITION.is_file() else ""
    for src in (comp_src, app_src):
        m = re.search(r"return useMemo\(\s*\n\s*\(\) => \(\{([\s\S]*?)\}\),", src)
        if m:
            return {ln.strip().rstrip(",") for ln in m.group(1).splitlines() if ln.strip()}
    raise SystemExit("studioEditorValue bag not found in useStudioEditorComposition.js or App.jsx")


def required_bag_keys(app_src: str, view_src: str) -> set[str]:
    bindings = app_bindings(app_src)
    imports = view_imports(view_src)
    bag = bag_destructure_keys(view_src)
    body = view_src[re.search(r"\} = useStudioEditor", view_src).end() :]
    tokens = set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)", body))
    used = {t for t in tokens if t not in SKIP and t not in imports and t not in FALSE_POSITIVE}
    return {t for t in used if t in bindings}


def check(app_src: str, view_src: str) -> int:
    required = required_bag_keys(app_src, view_src)
    bag_view = bag_destructure_keys(view_src)
    bag_memo = memo_keys(app_src)
    missing_in_view = sorted(required - bag_view)
    missing_in_memo = sorted(required - bag_memo)
    extra_in_view = sorted(bag_view - required)
    view_memo_diff = sorted(bag_view ^ bag_memo)

    ok = True
    if missing_in_view:
        ok = False
        print("ERROR: in App but missing from StudioEditorView destructure:", ", ".join(missing_in_view))
    if missing_in_memo:
        ok = False
        print("ERROR: in App but missing from studioEditorValue memo:", ", ".join(missing_in_memo))
    if view_memo_diff:
        ok = False
        print("ERROR: studioEditorValue memo keys differ from View destructure:", ", ".join(view_memo_diff[:20]))
        if len(view_memo_diff) > 20:
            print(f"  ... and {len(view_memo_diff) - 20} more")
    if extra_in_view and ok:
        print(f"note: {len(extra_in_view)} bag keys unused in view (ok during migration)")
    if ok:
        print(f"editor bag OK ({len(required)} required keys)")
    return 0 if ok else 1


def sync(app_src: str, view_src: str) -> None:
    required = required_bag_keys(app_src, view_src)
    all_keys = sorted(required)
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
    comp_src = COMPOSITION.read_text(encoding="utf-8") if COMPOSITION.is_file() else ""
    if COMPOSITION.is_file() and "useStudioEditorComposition" in comp_src:
        dest_params = ",\n    ".join(all_keys)
        comp_src = re.sub(
            r"export function useStudioEditorComposition\(\{\n[\s\S]*?\n\}\) \{\n  return useMemo\(\s*\n\s*\(\) => \(\{[\s\S]*?\}\),\s*\n\s*\[[\s\S]*?\],\s*\n\s*\);\n\}",
            f"""export function useStudioEditorComposition({{
  {dest_params},
}}) {{
  return useMemo(
    () => ({{
{bag_body}
    }}),
    [
      {deps},
    ],
  );
}}""",
            comp_src,
            count=1,
        )
        COMPOSITION.write_text(comp_src, encoding="utf-8")
        call_params = ",\n    ".join(all_keys)
        app_src = re.sub(
            r"const studioEditorValue = useStudioEditorComposition\(\{[\s\S]*?\}\);",
            f"const studioEditorValue = useStudioEditorComposition({{\n    {call_params},\n  }});",
            app_src,
            count=1,
        )
    else:
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
    print(f"synced {len(all_keys)} bag keys")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify bag; exit 1 on drift")
    parser.add_argument("--sync", action="store_true", help="Rewrite bag from view usage")
    args = parser.parse_args()
    app_src = APP.read_text(encoding="utf-8")
    view_src = VIEW.read_text(encoding="utf-8")
    if args.sync:
        sync(app_src, view_src)
        sys.exit(check(APP.read_text(encoding="utf-8"), VIEW.read_text(encoding="utf-8")))
    sys.exit(check(app_src, view_src))


if __name__ == "__main__":
    main()
