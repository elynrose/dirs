#!/usr/bin/env python3
"""Replace settings.default_tenant_id with auth.tenant_id in workflow phase routers."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "director_api" / "api" / "routers"

FILES = [
    "workflow_phase2.py",
    "workflow_phase3.py",
    "workflow_phase4.py",
    "workflow_phase5.py",
]

AUTH_IMPORT = "from director_api.auth.context import AuthContext\nfrom director_api.auth.deps import auth_context_dep"
TENANT_IMPORT = "from director_api.api.tenant_access import require_project_for_tenant"


def ensure_imports(text: str) -> str:
    if "auth_context_dep" not in text:
        text = text.replace(
            "from director_api.api.deps import meta_dep, settings_dep",
            "from director_api.api.deps import meta_dep, settings_dep\n" + AUTH_IMPORT,
        )
    if "require_project_for_tenant" not in text:
        anchor = "from director_api.api.deps import meta_dep, settings_dep"
        text = text.replace(anchor, anchor + "\n" + TENANT_IMPORT)
    return text


def inject_auth_param(text: str) -> str:
    """Add auth dep after meta_dep when missing in the same function signature block."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if re.match(r"\s+meta: dict = Depends\(meta_dep\),\s*$", line):
            # peek ahead until closing paren of def
            j = i + 1
            has_auth = False
            while j < len(lines) and not re.match(r"\s*\):\s*$", lines[j]):
                if "auth: AuthContext = Depends(auth_context_dep)" in lines[j]:
                    has_auth = True
                j += 1
            if not has_auth:
                indent = re.match(r"(\s+)", line).group(1)
                out.append(f"{indent}auth: AuthContext = Depends(auth_context_dep),")
        i += 1
    return "\n".join(out)


def migrate_helpers(text: str) -> str:
    text = re.sub(
        r"def _project_or_404\(db: Session, settings: Settings, project_id: UUID\) -> Project:\n"
        r"    p = db\.get\(Project, project_id\)\n"
        r"    if not p or p\.tenant_id != settings\.default_tenant_id:\n"
        r"        raise HTTPException\(status_code=404, detail=\{\"code\": \"NOT_FOUND\", \"message\": \"project not found\"\}\)\n"
        r"    return p\n",
        "",
        text,
    )
    text = text.replace(
        "_project_or_404(db, settings, project_id)",
        "require_project_for_tenant(db, project_id, auth.tenant_id)",
    )
    text = text.replace("p.tenant_id != settings.default_tenant_id", "p.tenant_id != auth.tenant_id")
    text = text.replace("a.tenant_id != settings.default_tenant_id", "a.tenant_id != auth.tenant_id")
    text = text.replace("r.tenant_id != settings.default_tenant_id", "r.tenant_id != auth.tenant_id")
    text = text.replace("tv.tenant_id != settings.default_tenant_id", "tv.tenant_id != auth.tenant_id")
    text = text.replace("mb.tenant_id != settings.default_tenant_id", "mb.tenant_id != auth.tenant_id")
    text = text.replace("issue.tenant_id != settings.default_tenant_id", "issue.tenant_id != auth.tenant_id")
    text = text.replace("MusicBed.tenant_id == settings.default_tenant_id", "MusicBed.tenant_id == auth.tenant_id")
    text = text.replace("TimelineVersion.tenant_id == settings.default_tenant_id", "TimelineVersion.tenant_id == auth.tenant_id")
    text = text.replace("CriticReport.tenant_id == settings.default_tenant_id", "CriticReport.tenant_id == auth.tenant_id")
    text = text.replace("settings.default_tenant_id", "auth.tenant_id")
    return text


def stamp_payload_tenant(text: str) -> str:
    """Ensure job payloads include tenant_id when they carry project/scene context."""
    return re.sub(
        r'payload=\{("project_id")',
        r'payload={"tenant_id": auth.tenant_id, \1',
        text,
    )


def migrate_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = ensure_imports(text)
    text = inject_auth_param(text)
    text = migrate_helpers(text)
    text = stamp_payload_tenant(text)
    path.write_text(text, encoding="utf-8")
    print(f"migrated {path.name}")


def main() -> None:
    for name in FILES:
        migrate_file(ROOT / name)


if __name__ == "__main__":
    main()
