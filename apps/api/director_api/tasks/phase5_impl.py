"""Phase 5 export preflight helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm.attributes import flag_modified

from director_api.db.models import Project, TimelineVersion

log = structlog.get_logger(__name__)


def _phase5_auto_heal_before_export(
    db: Any,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> dict[str, int]:
    """
    Reconcile timeline clips to viable scene media and auto-approve succeeded assets on disk when
    export preflight requires approval — persists DB + ``tv.timeline_json`` before readiness checks.
    """
    from director_api.services import timeline_image_repair as timeline_image_repair_svc

    stats = timeline_image_repair_svc.auto_heal_project_timeline_for_export(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )
    if (
        stats.get("relinked_assets")
        or stats.get("rebound_clips")
        or stats.get("storyboard_synced_clips")
        or stats.get("approved_scene_stills")
        or stats.get("approved_timeline_assets")
        or stats.get("reconciled_clips")
    ):
        if stats.get("reconciled_clips") or stats.get("rebound_clips") or stats.get("storyboard_synced_clips"):
            flag_modified(tv, "timeline_json")
        db.commit()
        db.refresh(tv)
        log.info(
            "phase5_export_auto_heal",
            project_id=str(project.id),
            timeline_version_id=str(tv.id),
            **stats,
        )
    return stats
