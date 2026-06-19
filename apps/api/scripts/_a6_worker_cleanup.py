"""Remove duplicate dead symbols from worker_tasks (canonical copies live in worker_runtime)."""
from __future__ import annotations

import ast
from pathlib import Path

WT = Path(__file__).resolve().parents[1] / "director_api/tasks/worker_tasks.py"
REMOVE = {
    "_agent_run_repair_failing_scenes",
    "_agent_run_repair_blocked_chapters",
}


def main() -> None:
    src = WT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    remove_ranges: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in REMOVE:
            remove_ranges.append((node.lineno - 1, node.end_lineno))
    remove_ranges.sort()
    new_lines: list[str] = []
    idx = 0
    for start, end in remove_ranges:
        new_lines.extend(lines[idx:start])
        idx = end
    new_lines.extend(lines[idx:])
    WT.write_text("".join(new_lines), encoding="utf-8")
    print(f"removed {len(remove_ranges)} duplicate functions")


if __name__ == "__main__":
    main()
