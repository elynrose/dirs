#!/usr/bin/env python3
"""One-shot: move studioEditorValue useMemo from App.jsx to useStudioEditorComposition.js."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/App.jsx"
HOOK = ROOT / "src/hooks/editor/useStudioEditorComposition.js"

app = APP.read_text(encoding="utf-8")
m = re.search(
    r"  const studioEditorValue = useMemo\(\s*\n\s*\(\) => \(\{([\s\S]*?)\}\),\s*\n\s*\[([\s\S]*?)\],\s*\n  \);",
    app,
)
if not m:
    raise SystemExit("studioEditorValue useMemo not found in App.jsx")

bag_body = m.group(1)
deps_body = m.group(2)
keys = [ln.strip().rstrip(",") for ln in bag_body.splitlines() if ln.strip()]
deps = [ln.strip().rstrip(",") for ln in deps_body.splitlines() if ln.strip()]

HOOK.parent.mkdir(parents=True, exist_ok=True)
dep_destructure = ",\n    ".join(keys)
bag_lines = "\n".join(f"      {k}," for k in keys)
dep_array = ",\n      ".join(deps)
content = f"""import {{ useMemo }} from "react";

/**
 * Builds the Studio editor context bag. State still lives in App.jsx until domain hooks (A1) land.
 */
export function useStudioEditorComposition({{
  {dep_destructure},
}}) {{
  return useMemo(
    () => ({{
{bag_lines}
    }}),
    [
      {dep_array},
    ],
  );
}}
"""
HOOK.write_text(content, encoding="utf-8")

import_line = 'import { useStudioEditorComposition } from "./hooks/editor/useStudioEditorComposition.js";'
if import_line not in app:
    app = app.replace(
        'import { useStudioCharacters } from "./hooks/useStudioCharacters.js";',
        'import { useStudioCharacters } from "./hooks/useStudioCharacters.js";\n' + import_line,
    )

call_block = f"""  const studioEditorValue = useStudioEditorComposition({{
    {dep_destructure},
  }});
"""
app = re.sub(r"  const studioEditorValue = useMemo\([\s\S]*?\n  \);\n", call_block + "\n", app, count=1)
APP.write_text(app, encoding="utf-8")
print(f"extracted {len(keys)} keys to {HOOK.relative_to(ROOT)}")
