"""Repair timeline clips that reference broken or disallowed scene images (rough-cut preflight helpers)."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import Asset, Chapter, Project, Scene, TimelineVersion
from director_api.services.runtime_settings import scene_vo_tail_padding_sec_for_tenant
from director_api.services.scene_timeline_duration import effective_scene_visual_budget_sec
from director_api.services.phase5_readiness import (
    collect_timeline_export_attention_assets,
    get_timeline_asset_for_project,
    parse_assets_layout_project_scene,
    timeline_visual_asset_issue_codes,
)
from director_api.validation.timeline_schema import validate_timeline_document
from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file

log = structlog.get_logger(__name__)

REPAIR_ISSUE_CODES = frozenset(
    {
        "timeline_asset_not_approved",
        "timeline_asset_file_missing",
        "timeline_clip_not_visual_asset",
        "timeline_asset_rejected_or_failed",
    }
)


def filter_flagged_timeline_image_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows from ``collect_timeline_export_attention_assets`` eligible for reject + regen."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("asset_type") or "").lower() != "image":
            continue
        if not row.get("scene_id"):
            continue
        codes = set(row.get("issue_codes") or [])
        if not codes & REPAIR_ISSUE_CODES:
            continue
        out.append(row)
    return out


def reject_asset_for_repair(db: Session, asset: Asset, *, reason: str = "Rough-cut image repair") -> None:
    """Match ``POST /v1/assets/{id}/reject`` semantics."""
    asset.approved_at = None
    asset.status = "rejected"
    pj = dict(asset.params_json) if isinstance(asset.params_json, dict) else {}
    pj["rejection"] = {
        "reason": (reason or "")[:8000],
        "rejected_at": datetime.now(timezone.utc).isoformat(),
    }
    asset.params_json = pj


def pick_best_scene_image(
    db: Session,
    *,
    scene_id: UUID,
    project_id: UUID,
    tenant_id: str,
    allow_unapproved_media: bool,
    exclude_asset_ids: set[UUID] | None = None,
    storage_root: Path | None = None,
) -> Asset | None:
    """
    Best image on the scene for timeline clips.

    When ``storage_root`` is set, eligibility matches export preflight via
    :func:`timeline_visual_asset_issue_codes` (including ``approved_at`` + file while ``status`` is
    still ``rejected``/``failed``). Do not filter those rows out in SQL.
    When ``storage_root`` is None (DB-only rebound), keeps the stricter non-rejected/failed query.
    """
    ex = exclude_asset_ids or set()
    q = (
        select(Asset)
        .join(Scene, Asset.scene_id == Scene.id)
        .join(Chapter, Scene.chapter_id == Chapter.id)
        .where(
            Asset.scene_id == scene_id,
            Chapter.project_id == project_id,
            Asset.tenant_id == tenant_id,
            func.lower(Asset.asset_type) == "image",
        )
    )
    if storage_root is None:
        q = q.where(Asset.status.notin_(["rejected", "failed"]))
    rows = list(db.scalars(q).all())
    rows = [a for a in rows if a.id not in ex]
    if storage_root is not None:
        rows = [
            a
            for a in rows
            if not timeline_visual_asset_issue_codes(
                a, storage_root=storage_root, allow_unapproved_media=allow_unapproved_media
            )
        ]
    else:
        rows = [a for a in rows if a.status == "succeeded"]
        if not allow_unapproved_media:
            rows = [a for a in rows if a.approved_at is not None]
    if not rows:
        return None

    def sort_key(a: Asset) -> tuple:
        has_ap = 1 if a.approved_at else 0
        ap_ts = a.approved_at.timestamp() if a.approved_at else 0.0
        cr_ts = a.created_at.timestamp() if a.created_at else 0.0
        return (-has_ap, -ap_ts, -(a.timeline_sequence or 0), -cr_ts)

    rows.sort(key=sort_key)
    return rows[0]


def pick_primary_export_asset_for_scene(
    db: Session,
    *,
    scene_id: UUID,
    project_id: UUID,
    tenant_id: str,
    allow_unapproved_media: bool,
    storage_root: Path,
) -> Asset | None:
    """Video (if any) else best image; must pass export-style checks on disk (see :func:`timeline_visual_asset_issue_codes`)."""
    videos = list(
        db.scalars(
            select(Asset)
            .join(Scene, Asset.scene_id == Scene.id)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(
                Asset.scene_id == scene_id,
                Chapter.project_id == project_id,
                Asset.tenant_id == tenant_id,
                func.lower(Asset.asset_type) == "video",
            )
            .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
        ).all()
    )
    for v in videos:
        if timeline_visual_asset_issue_codes(
            v, storage_root=storage_root, allow_unapproved_media=allow_unapproved_media
        ):
            continue
        return v
    return pick_best_scene_image(
        db,
        scene_id=scene_id,
        project_id=project_id,
        tenant_id=tenant_id,
        allow_unapproved_media=allow_unapproved_media,
        storage_root=storage_root,
    )


def pick_scene_export_asset_db_only(
    db: Session,
    *,
    scene_id: UUID,
    project_id: UUID,
    tenant_id: str,
) -> Asset | None:
    """
    Succeeded scene video or best succeeded image with no disk check.

    Used when orphan clips must be rebound but ``storage_url`` does not map under
    ``local_storage_root`` (e.g. http/minio keys) so :func:`pick_primary_export_asset_for_scene`
    returns nothing. Preflight may then report ``timeline_asset_file_missing`` instead of
    ``timeline_asset_not_in_project``.
    """
    for lax in (False, True):
        videos = list(
            db.scalars(
                select(Asset)
                .join(Scene, Asset.scene_id == Scene.id)
                .join(Chapter, Scene.chapter_id == Chapter.id)
                .where(
                    Asset.scene_id == scene_id,
                    Chapter.project_id == project_id,
                    Asset.tenant_id == tenant_id,
                    func.lower(Asset.asset_type) == "video",
                    Asset.status == "succeeded",
                )
                .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
            ).all()
        )
        for v in videos:
            if not lax and v.approved_at is None:
                continue
            return v
        img = pick_best_scene_image(
            db,
            scene_id=scene_id,
            project_id=project_id,
            tenant_id=tenant_id,
            allow_unapproved_media=lax,
        )
        if img is not None:
            return img
    return None


def _timeline_clip_order_key(c: dict[str, Any]) -> int:
    v = c.get("order_index", 0)
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def pick_rebind_replacement_for_scene(
    db: Session,
    *,
    scene_id: UUID,
    project: Project,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> Asset | None:
    """Disk-first primary pick, lax disk pick, then DB-only pick for one scene."""
    rep = pick_primary_export_asset_for_scene(
        db,
        scene_id=scene_id,
        project_id=project.id,
        tenant_id=project.tenant_id,
        allow_unapproved_media=allow_unapproved_media,
        storage_root=storage_root,
    )
    if rep is None and not allow_unapproved_media:
        rep = pick_primary_export_asset_for_scene(
            db,
            scene_id=scene_id,
            project_id=project.id,
            tenant_id=project.tenant_id,
            allow_unapproved_media=True,
            storage_root=storage_root,
        )
    if rep is None:
        rep = pick_scene_export_asset_db_only(
            db,
            scene_id=scene_id,
            project_id=project.id,
            tenant_id=project.tenant_id,
        )
    return rep


def pick_rebind_replacement_scan_scenes(
    db: Session,
    *,
    scenes: list[Scene],
    preferred_index: int,
    project: Project,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> Asset | None:
    """Try the preferred story-order scene first, then every other scene."""
    if not scenes:
        return None
    ix = min(max(preferred_index, 0), len(scenes) - 1)
    primary = scenes[ix]
    for sc in [primary] + [s for s in scenes if s.id != primary.id]:
        rep = pick_rebind_replacement_for_scene(
            db,
            scene_id=sc.id,
            project=project,
            storage_root=storage_root,
            allow_unapproved_media=allow_unapproved_media,
        )
        if rep is not None:
            return rep
    return None


def pick_any_project_succeeded_visual(
    db: Session,
    *,
    project_id: UUID,
    tenant_id: str,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> Asset | None:
    """
    Last-resort rebound target: any project video/image that passes export-style checks on disk.

    Resolves clips when media exists but scene linkage/order does not match the timeline.
    """
    passes = (False, True) if not allow_unapproved_media else (True,)
    for lax in passes:
        for atype in ("video", "image"):
            rows = list(
                db.scalars(
                    select(Asset)
                    .where(
                        Asset.project_id == project_id,
                        Asset.tenant_id == tenant_id,
                        func.lower(Asset.asset_type) == atype,
                    )
                    .order_by(Asset.created_at.desc())
                ).all()
            )
            for a in rows:
                if timeline_visual_asset_issue_codes(
                    a, storage_root=storage_root, allow_unapproved_media=lax
                ):
                    continue
                return a
    return None


def _rebind_succeeded_visual_counts(db: Session, project: Project) -> tuple[int, int]:
    """(succeeded image/video on scene graph matching project.tenant_id, same ignoring tenant filter)."""
    base_where = [
        Chapter.project_id == project.id,
        Asset.status == "succeeded",
        func.lower(Asset.asset_type).in_(["image", "video"]),
    ]
    n_any = int(
        db.scalar(
            select(func.count())
            .select_from(Asset)
            .join(Scene, Asset.scene_id == Scene.id)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(*base_where)
        )
        or 0
    )
    n_tenant = int(
        db.scalar(
            select(func.count())
            .select_from(Asset)
            .join(Scene, Asset.scene_id == Scene.id)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(*base_where, Asset.tenant_id == project.tenant_id)
        )
        or 0
    )
    return n_tenant, n_any


def rebind_orphan_timeline_clips_by_scene_order(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> int:
    """
    For clips whose ``asset_id`` does not resolve for this project (missing row / wrong project),
    rebind to viable media: preferred scene by clip index, then any scene in story order, then any
    succeeded project video/image (``Asset.project_id``) as a last resort.
    """
    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project.id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    if not scenes:
        return 0
    slots = build_visual_timeline_slots(
        db,
        project=project,
        scenes=scenes,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    clips = tj.get("clips")
    if not isinstance(clips, list):
        return 0
    ordered = sorted((c for c in clips if isinstance(c, dict)), key=_timeline_clip_order_key)
    asset_clips: list[dict[str, Any]] = []
    for c in ordered:
        src = c.get("source")
        if isinstance(src, dict) and src.get("kind") == "asset":
            asset_clips.append(c)
    updated = 0
    unresolved_orphan_ids: list[UUID] = []
    for j, c in enumerate(asset_clips):
        src = c.get("source")
        if not isinstance(src, dict):
            continue
        try:
            aid = UUID(str(src.get("asset_id")))
        except (TypeError, ValueError):
            continue
        if get_timeline_asset_for_project(db, aid, project.id) is not None:
            continue
        if j < len(slots):
            scene_ix = _scene_index_for_id(scenes, slots[j][0])
        else:
            scene_ix = min(j, len(scenes) - 1)
        rep = pick_rebind_replacement_scan_scenes(
            db,
            scenes=scenes,
            preferred_index=scene_ix,
            project=project,
            storage_root=storage_root,
            allow_unapproved_media=allow_unapproved_media,
        )
        if rep is None:
            rep = pick_any_project_succeeded_visual(
                db,
                project_id=project.id,
                tenant_id=project.tenant_id,
                storage_root=storage_root,
                allow_unapproved_media=allow_unapproved_media,
            )
        if rep is None and not allow_unapproved_media:
            rep = pick_any_project_succeeded_visual(
                db,
                project_id=project.id,
                tenant_id=project.tenant_id,
                storage_root=storage_root,
                allow_unapproved_media=True,
            )
        if rep is None:
            unresolved_orphan_ids.append(aid)
            continue
        src["asset_id"] = str(rep.id)
        updated += 1
    if unresolved_orphan_ids:
        n_tenant, n_any = _rebind_succeeded_visual_counts(db, project)
        log.warning(
            "rebind_orphan_clips_unresolved",
            project_id=str(project.id),
            timeline_version_id=str(tv.id),
            orphan_clip_count=len(unresolved_orphan_ids),
            first_orphan_asset_id=str(unresolved_orphan_ids[0]),
            succeeded_visuals_scene_graph_matching_tenant=n_tenant,
            succeeded_visuals_scene_graph_any_tenant=n_any,
            hint=(
                "If matching_tenant is 0 but any_tenant > 0, assets.tenant_id likely mismatches projects.tenant_id. "
                "If both are 0 here but the studio UI shows media, the Celery worker probably uses a different "
                "database or storage root than the API."
            ),
        )
    if updated:
        validate_timeline_document(tj)
        tv.timeline_json = tj
        db.add(tv)
    return updated


def list_export_ready_scene_visuals_ordered(
    db: Session,
    *,
    scene_id: UUID,
    project_id: UUID,
    tenant_id: str,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> list[Asset]:
    """
    Approved (unless ``allow_unapproved_media``) image/video rows on the scene that pass export-style
    checks, ordered by ``timeline_sequence``, then video-before-image, then ``created_at``.
    """
    rows = list(
        db.scalars(
            select(Asset)
            .join(Scene, Asset.scene_id == Scene.id)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(
                Asset.scene_id == scene_id,
                Chapter.project_id == project_id,
                Asset.tenant_id == tenant_id,
                func.lower(Asset.asset_type).in_(["image", "video"]),
            )
        ).all()
    )

    def sort_key(a: Asset) -> tuple:
        at = str(a.asset_type or "").lower()
        type_prio = 0 if at == "video" else 1
        seq = a.timeline_sequence or 0
        ct = a.created_at.timestamp() if a.created_at else 0.0
        return (seq, type_prio, ct)

    rows.sort(key=sort_key)
    return [
        a
        for a in rows
        if not timeline_visual_asset_issue_codes(
            a, storage_root=storage_root, allow_unapproved_media=allow_unapproved_media
        )
    ]


def build_visual_timeline_slots(
    db: Session,
    *,
    project: Project,
    scenes: list[Scene],
    storage_root: Path,
    allow_unapproved_media: bool,
) -> list[tuple[UUID, Asset | None]]:
    """
    Planned visual clips in story order.

    Each entry is ``(scene_id, asset_or_none)``. When ``asset_or_none`` is set, that clip should use
    the asset directly. When ``None``, callers pick primary media via scan/fallback (one slot per scene).

    With ``project.use_all_approved_scene_media``, a scene with N export-ready visuals produces N slots;
    a scene with none still produces one fallback slot.
    """
    out: list[tuple[UUID, Asset | None]] = []
    use_all = bool(getattr(project, "use_all_approved_scene_media", False))
    for sc in scenes:
        if use_all:
            ready = list_export_ready_scene_visuals_ordered(
                db,
                scene_id=sc.id,
                project_id=project.id,
                tenant_id=project.tenant_id,
                storage_root=storage_root,
                allow_unapproved_media=allow_unapproved_media,
            )
            if ready:
                for a in ready:
                    out.append((sc.id, a))
            else:
                out.append((sc.id, None))
        else:
            out.append((sc.id, None))
    return out


def _scene_index_for_id(scenes: list[Scene], scene_id: UUID) -> int:
    for i, s in enumerate(scenes):
        if s.id == scene_id:
            return i
    return max(0, len(scenes) - 1)


def ordered_project_scenes(db: Session, *, project_id: UUID) -> list[Scene]:
    return list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )


def sync_timeline_visual_clips_from_storyboard(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> int:
    """
    Rebuild visual ``clips`` from story-order scenes.

    Default: one asset clip per scene (primary media via scan + project fallbacks). With
    ``project.use_all_approved_scene_media``: one clip per export-ready approved visual per scene.

    Preserves timing fields from the previous clip at each index when present.
    """
    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project.id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    if not scenes:
        return 0
    vo_tail = scene_vo_tail_padding_sec_for_tenant(db, project.tenant_id)
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    raw_clips = tj.get("clips")
    old_list = [c for c in raw_clips if isinstance(c, dict)] if isinstance(raw_clips, list) else []
    old_asset: list[dict[str, Any]] = []
    for c in sorted(old_list, key=_timeline_clip_order_key):
        src = c.get("source")
        if isinstance(src, dict) and src.get("kind") == "asset":
            old_asset.append(copy.deepcopy(c))

    slots = build_visual_timeline_slots(
        db,
        project=project,
        scenes=scenes,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )

    new_clips: list[dict[str, Any]] = []
    updated = 0
    last_rep: Asset | None = None
    for idx, (scene_id, fixed_asset) in enumerate(slots):
        pref_j = _scene_index_for_id(scenes, scene_id)
        if fixed_asset is not None:
            rep = fixed_asset
        else:
            rep = pick_rebind_replacement_scan_scenes(
                db,
                scenes=scenes,
                preferred_index=pref_j,
                project=project,
                storage_root=storage_root,
                allow_unapproved_media=allow_unapproved_media,
            )
            if rep is None:
                rep = pick_any_project_succeeded_visual(
                    db,
                    project_id=project.id,
                    tenant_id=project.tenant_id,
                    storage_root=storage_root,
                    allow_unapproved_media=allow_unapproved_media,
                )
            if rep is None and not allow_unapproved_media:
                rep = pick_any_project_succeeded_visual(
                    db,
                    project_id=project.id,
                    tenant_id=project.tenant_id,
                    storage_root=storage_root,
                    allow_unapproved_media=True,
                )
            if rep is None:
                rep = last_rep
        if rep is not None:
            last_rep = rep

        if idx < len(old_asset):
            base = copy.deepcopy(old_asset[idx])
        else:
            sc_match = next((s for s in scenes if s.id == scene_id), None)
            if sc_match is not None:
                d_budget = effective_scene_visual_budget_sec(
                    db,
                    scene=sc_match,
                    project_id=project.id,
                    base_clip_sec=10.0,
                    storage_root=storage_root,
                    tail_padding_sec=vo_tail,
                )
            else:
                d_budget = 10.0
            base = {"order_index": idx, "duration_sec": d_budget, "source": {"kind": "asset", "asset_id": ""}}

        base["order_index"] = idx
        if not isinstance(base.get("source"), dict):
            base["source"] = {"kind": "asset", "asset_id": ""}
        base["source"]["kind"] = "asset"
        old_aid = str(base["source"].get("asset_id") or "")
        if rep is not None:
            base["source"]["asset_id"] = str(rep.id)
            if old_aid != str(rep.id):
                updated += 1
        new_clips.append(base)

    tj_out = copy.deepcopy(tj)
    tj_out["clips"] = new_clips
    validate_timeline_document(tj_out)
    tv.timeline_json = tj_out
    db.add(tv)
    return updated


def clip_visual_needs_replacement(
    asset: Asset,
    *,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> bool:
    """
    True if this asset ref fails export preflight for a visual timeline clip — including wrong/missing
    ``asset_type`` (e.g. ``timeline_clip_not_visual_asset`` in preflight). Must match
    :func:`timeline_visual_asset_issue_codes` so **Reconcile** actually fixes rows the checklist reports.
    """
    return bool(
        timeline_visual_asset_issue_codes(
            asset, storage_root=storage_root, allow_unapproved_media=allow_unapproved_media
        )
    )


def pick_replacement_visual_for_timeline_clip(
    db: Session,
    *,
    scene_id: UUID,
    project: Project,
    storage_root: Path,
    allow_unapproved_media: bool,
    exclude_asset_ids: set[UUID],
) -> Asset | None:
    """
    Pick another clip target on the same scene that passes export-style visual checks (see
    :func:`timeline_visual_asset_issue_codes`, including stale ``rejected``/``failed`` rows with
    ``approved_at`` and a readable file). Prefer **video**, then **image** (higher
    ``timeline_sequence`` first, then newer ``created_at``). Skip ids in ``exclude_asset_ids``.
    """
    ex = exclude_asset_ids or set()
    tenant_id = project.tenant_id
    project_id = project.id

    def pick_pool(*, allow_unapproved_codes: bool) -> Asset | None:
        for atype in ("video", "image"):
            stmt = (
                select(Asset)
                .join(Scene, Asset.scene_id == Scene.id)
                .join(Chapter, Scene.chapter_id == Chapter.id)
                .where(
                    Asset.scene_id == scene_id,
                    Chapter.project_id == project_id,
                    Asset.tenant_id == tenant_id,
                    func.lower(Asset.asset_type) == atype,
                )
                .order_by(Asset.timeline_sequence.desc(), Asset.created_at.desc())
            )
            if not allow_unapproved_codes:
                stmt = stmt.where(Asset.approved_at.isnot(None))
            for row in db.scalars(stmt).all():
                if row.id in ex:
                    continue
                if timeline_visual_asset_issue_codes(
                    row,
                    storage_root=storage_root,
                    allow_unapproved_media=allow_unapproved_codes,
                ):
                    continue
                return row
        return None

    picked = pick_pool(allow_unapproved_codes=False)
    if picked is not None:
        return picked
    if allow_unapproved_media:
        return pick_pool(allow_unapproved_codes=True)
    return None


def reconcile_timeline_clip_images(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> tuple[int, int]:
    """
    Point each clip at a **viable** scene video (preferred) or image when the current ref fails export
    checks. Uses :func:`pick_replacement_visual_for_timeline_clip`.

    Returns ``(updated_clips, unchanged_clips)``.
    """
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    clips = tj.get("clips")
    if not isinstance(clips, list):
        return 0, 0

    updated = 0
    unchanged = 0
    for c in clips:
        if not isinstance(c, dict):
            unchanged += 1
            continue
        src = c.get("source")
        if not isinstance(src, dict) or src.get("kind") != "asset":
            unchanged += 1
            continue
        try:
            aid = UUID(str(src.get("asset_id")))
        except (TypeError, ValueError):
            unchanged += 1
            continue

        asset = get_timeline_asset_for_project(db, aid, project.id)
        if asset is None or asset.scene_id is None:
            unchanged += 1
            continue

        if not clip_visual_needs_replacement(
            asset, storage_root=storage_root, allow_unapproved_media=allow_unapproved_media
        ):
            unchanged += 1
            continue

        rep = pick_replacement_visual_for_timeline_clip(
            db,
            scene_id=asset.scene_id,
            project=project,
            storage_root=storage_root,
            allow_unapproved_media=allow_unapproved_media,
            exclude_asset_ids={aid},
        )
        if rep is None:
            scenes_ord = ordered_project_scenes(db, project_id=project.id)
            if scenes_ord:
                pref = _scene_index_for_id(scenes_ord, asset.scene_id)
                rep = pick_rebind_replacement_scan_scenes(
                    db,
                    scenes=scenes_ord,
                    preferred_index=pref,
                    project=project,
                    storage_root=storage_root,
                    allow_unapproved_media=allow_unapproved_media,
                )
            if rep is None:
                rep = pick_any_project_succeeded_visual(
                    db,
                    project_id=project.id,
                    tenant_id=project.tenant_id,
                    storage_root=storage_root,
                    allow_unapproved_media=allow_unapproved_media,
                )
            if rep is None and not allow_unapproved_media:
                rep = pick_any_project_succeeded_visual(
                    db,
                    project_id=project.id,
                    tenant_id=project.tenant_id,
                    storage_root=storage_root,
                    allow_unapproved_media=True,
                )
        if rep is None or rep.id == aid:
            unchanged += 1
            continue

        src["asset_id"] = str(rep.id)
        updated += 1

    if updated:
        validate_timeline_document(tj)
        tv.timeline_json = tj
        db.add(tv)

    return updated, unchanged


def _approve_asset_clear_rejection(asset: Asset, *, now: datetime) -> None:
    """Match ``POST /v1/assets/{id}/approve`` field updates."""
    asset.approved_at = now
    pj = dict(asset.params_json) if isinstance(asset.params_json, dict) else {}
    pj.pop("rejection", None)
    asset.params_json = pj


def auto_approve_timeline_clip_assets(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
) -> int:
    """
    Approve succeeded image/video rows referenced by ``tv.timeline_json`` clips when the file exists on disk.
    Used before rough/final export when strict approval is required.
    """
    now = datetime.now(timezone.utc)
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    clips = tj.get("clips")
    if not isinstance(clips, list):
        return 0
    seen: set[UUID] = set()
    n = 0
    for c in clips:
        if not isinstance(c, dict):
            continue
        src = c.get("source")
        if not isinstance(src, dict) or src.get("kind") != "asset":
            continue
        try:
            aid = UUID(str(src.get("asset_id")))
        except (TypeError, ValueError):
            continue
        asset = get_timeline_asset_for_project(db, aid, project.id)
        if asset is None or asset.id in seen:
            continue
        seen.add(asset.id)
        if asset.approved_at is not None:
            continue
        if str(asset.asset_type or "").lower() not in ("image", "video"):
            continue
        if asset.status != "succeeded":
            continue
        lp = path_from_storage_url(asset.storage_url or "", storage_root=storage_root)
        if lp is None or not path_is_readable_file(lp):
            continue
        _approve_asset_clear_rejection(asset, now=now)
        db.add(asset)
        n += 1
    return n


def auto_approve_succeeded_scene_stills_on_disk(
    db: Session,
    *,
    project_id: UUID,
    tenant_id: str,
    storage_root: Path,
) -> int:
    """
    For each scene in the project with no approved succeeded image yet, approve one succeeded image
    that already exists on disk (prefers higher ``timeline_sequence``, then newest).
    Satisfies export preflight ``missing_approved_scene_image`` when stills are present but not clicked through approval.
    """
    now = datetime.now(timezone.utc)
    n = 0
    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    for sc in scenes:
        assets = list(
            db.scalars(
                select(Asset).where(
                    Asset.scene_id == sc.id,
                    Asset.tenant_id == tenant_id,
                    func.lower(Asset.asset_type) == "image",
                    Asset.status == "succeeded",
                )
            ).all()
        )
        if any(a.approved_at is not None for a in assets):
            continue
        candidates: list[Asset] = []
        for a in assets:
            lp = path_from_storage_url(a.storage_url or "", storage_root=storage_root)
            if lp is not None and path_is_readable_file(lp):
                candidates.append(a)
        if not candidates:
            continue

        def _created_ts(a: Asset) -> float:
            return a.created_at.timestamp() if a.created_at else 0.0

        candidates.sort(key=lambda a: (-(a.timeline_sequence or 0), -_created_ts(a)))
        pick = candidates[0]
        _approve_asset_clear_rejection(pick, now=now)
        db.add(pick)
        n += 1
    return n


def repair_timeline_clip_assets_from_storage_paths(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
) -> int:
    """
    Fix ``Asset.project_id`` / ``scene_id`` when the row drifted but ``storage_url`` still uses the
    canonical ``assets/<project_id>/<scene_id>/…`` layout for this project.
    """
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    clips = tj.get("clips")
    if not isinstance(clips, list):
        return 0
    n = 0
    for c in clips:
        if not isinstance(c, dict):
            continue
        src = c.get("source")
        if not isinstance(src, dict) or src.get("kind") != "asset":
            continue
        try:
            aid = UUID(str(src.get("asset_id")))
        except (TypeError, ValueError):
            continue
        a = db.get(Asset, aid)
        if a is None or a.tenant_id != project.tenant_id:
            continue
        path_pid, path_sid = parse_assets_layout_project_scene(a.storage_url)
        if path_pid != project.id:
            continue
        touched = False
        if a.project_id != project.id:
            a.project_id = project.id
            touched = True
        if path_sid is not None:
            sc = db.get(Scene, path_sid)
            if sc is not None:
                ch = db.get(Chapter, sc.chapter_id)
                if ch is not None and ch.project_id == project.id and a.scene_id != path_sid:
                    a.scene_id = path_sid
                    touched = True
        if touched:
            db.add(a)
            n += 1
    return n


def run_timeline_clip_reconcile_pipeline(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> dict[str, int]:
    """
    Single sequence: relink asset rows from storage paths → auto-approve scene stills (strict mode) →
    storyboard sync → orphan rebind → per-clip reconcile → auto-approve timeline refs (strict mode).
    """
    relinked = repair_timeline_clip_assets_from_storage_paths(db, project=project, tv=tv)
    approved_stills = 0
    if not allow_unapproved_media:
        approved_stills = auto_approve_succeeded_scene_stills_on_disk(
            db,
            project_id=project.id,
            tenant_id=project.tenant_id,
            storage_root=storage_root,
        )
    storyboard_synced = sync_timeline_visual_clips_from_storyboard(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )
    rebounded = rebind_orphan_timeline_clips_by_scene_order(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )
    updated, unchanged = reconcile_timeline_clip_images(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )
    approved_on_timeline = 0
    if not allow_unapproved_media:
        approved_on_timeline = auto_approve_timeline_clip_assets(
            db,
            project=project,
            tv=tv,
            storage_root=storage_root,
        )
    return {
        "relinked_assets": relinked,
        "approved_scene_stills": approved_stills,
        "storyboard_synced_clips": storyboard_synced,
        "rebound_clips": rebounded,
        "updated_clips": updated,
        "unchanged_clips": unchanged,
        "approved_timeline_clip_assets": approved_on_timeline,
    }


def auto_heal_project_timeline_for_export(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> dict[str, int]:
    """
    Before rough/fine/final export: approve on-disk scene stills / timeline refs (when approval is required),
    reconcile clips onto viable scene media, persist timeline JSON updates.
    """
    p = run_timeline_clip_reconcile_pipeline(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )
    return {
        "relinked_assets": p["relinked_assets"],
        "storyboard_synced_clips": p["storyboard_synced_clips"],
        "rebound_clips": p["rebound_clips"],
        "approved_scene_stills": p["approved_scene_stills"],
        "reconciled_clips": p["updated_clips"],
        "approved_timeline_assets": p["approved_timeline_clip_assets"],
    }


def collect_repair_candidates(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> list[dict[str, Any]]:
    raw = collect_timeline_export_attention_assets(
        db,
        project=project,
        tv=tv,
        storage_root=storage_root,
        allow_unapproved_media=allow_unapproved_media,
    )
    return filter_flagged_timeline_image_rows(raw)
