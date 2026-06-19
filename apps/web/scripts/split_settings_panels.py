#!/usr/bin/env python3
"""Split StudioSettingsPage.jsx tab bodies into lazy-loaded panel files."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/components/StudioSettingsPage.jsx"
OUT = ROOT / "src/components/settings"
GEN = OUT / "generation"

text = SRC.read_text(encoding="utf-8")
lines = text.splitlines()

IMPORTS = text.split("export function StudioSettingsPage")[0].strip()
DESTRUCT_INNER = lines[31:102]  # accountProfile … uploadComfyuiWorkflowFile

PANELS: list[tuple[str, Path, int, int]] = [
    ("SettingsAutomationPanel", OUT, 730, 1056),
    ("SettingsStudioPanel", OUT, 1059, 1101),
    ("SettingsIntegrationsPanel", OUT, 1104, 2416),
    ("SettingsVoiceRefPanel", OUT, 2419, 2516),
    ("SettingsGenerationEnginesPanel", GEN, 195, 432),
    ("SettingsGenerationNarrationStylesPanel", GEN, 435, 618),
    ("SettingsGenerationVisualPanel", GEN, 620, 726),
]


def fix_imports(block: str, *, info_up: int, lib_up: int) -> str:
    block = block.replace('from "./InfoTip.jsx"', f'from "{"../" * info_up}InfoTip.jsx"')
    block = block.replace('from "../lib/', f'from "{"../" * lib_up}lib/')
    return block


def destruct_block() -> str:
    inner = "\n".join(f"    {line}" for line in DESTRUCT_INNER)
    return f"  const {{\n{inner}\n  }} = p;"


def wrap(name: str, body_lines: list[str], *, info_up: int, lib_up: int) -> str:
    body = "\n".join(f"    {line}" for line in body_lines)
    imports = fix_imports(IMPORTS, info_up=info_up, lib_up=lib_up)
    return (
        f"{imports}\n\n"
        f"/** Lazy-loaded Settings sub-panel ({name}). */\n"
        f"export default function {name}(props) {{\n"
        f"  const p = props.p ?? props;\n"
        f"{destruct_block()}\n"
        f"  return (\n"
        f"{body}\n"
        f"  );\n"
        f"}}\n"
    )


def write_lazy_index(dir_path: Path, names: list[str], rel_prefix: str = "./") -> None:
    rows = ['import { lazy } from "react";', ""]
    for name in names:
        rows.append(
            f'export const Lazy{name} = lazy(() => import("{rel_prefix}{name}.jsx"));'
        )
    (dir_path / "lazyPanels.js").write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    GEN.mkdir(parents=True, exist_ok=True)

    top_names: list[str] = []
    gen_names: list[str] = []

    for name, out_dir, start, end in PANELS:
        info_up = 2 if out_dir == GEN else 1
        lib_up = 3 if out_dir == GEN else 2
        chunk = lines[start - 1 : end]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.jsx").write_text(wrap(name, chunk, info_up=info_up, lib_up=lib_up), encoding="utf-8")
        print(f"wrote {out_dir.name}/{name}.jsx")
        if out_dir == GEN:
            gen_names.append(name)
        else:
            top_names.append(name)

    write_lazy_index(OUT, top_names)
    write_lazy_index(GEN, gen_names)
    print("done")


if __name__ == "__main__":
    main()
