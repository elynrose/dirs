"""Remove on-disk artifacts when a project is deleted."""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


def remove_generated_project_files(storage_root: str | Path, project_id: UUID) -> None:
    """Remove local files generated for a project (under ``local_storage_root``).

    Deletes ``assets/<project_id>`` and ``narrations/<project_id>`` only.
    Does **not** remove ``exports/<project_id>`` so rough/fine/final cuts and other exports remain on disk.
    """
    root = Path(storage_root).resolve()
    pid = str(project_id)
    for sub in ("assets", "narrations"):
        target = (root / sub / pid).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            log.warning("project_storage_cleanup_skip_unsafe_path", path=str(target))
            continue
        if not target.is_dir():
            continue
        try:
            shutil.rmtree(target)
            log.info("project_storage_cleanup_removed", path=str(target))
        except OSError as e:
            log.warning("project_storage_cleanup_failed", path=str(target), error=str(e))
