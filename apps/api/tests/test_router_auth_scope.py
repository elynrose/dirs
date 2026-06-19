"""Router helpers must not reference ``auth`` without receiving it as a parameter."""

from __future__ import annotations

import ast
from pathlib import Path


def _router_auth_scope_violations() -> list[tuple[str, int, str]]:
    root = Path(__file__).resolve().parents[1] / "director_api" / "api" / "routers"
    issues: list[tuple[str, int, str]] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "auth"
                    and "auth" not in params
                ):
                    issues.append((path.name, node.lineno, node.name))
    return issues


def test_router_functions_do_not_use_auth_without_param():
    assert _router_auth_scope_violations() == []
