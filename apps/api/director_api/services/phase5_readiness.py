"""Phase 5 export readiness: deterministic structural checks (no LLM / critic gate)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from jsonschema import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.db.models import Asset, Chapter, MusicBed, NarrationTrack, Project, Scene, TimelineVersion
from director_api.validation.timeline_schema import validate_timeline_document
from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file

ExportStage = Literal["rough_cut", "fine_cut", "final_cut"]


def parse_assets_layout_project_scene(url: str | None) -> tuple[UUID | None, UUID | None]:
    """
    From a storage URL or relative key, extract ``(project_id, scene_id)`` after the ``…/assets/`` segment
    when the layout is ``assets/<project_id>/<scene_id>/…`` (local-first disk layout).
    """
    if not url or not str(url).strip():
        return None, None
    raw = str(url).strip().replace("\\", "/")
    lower = raw.lower()
    idx = lower.find("/assets/")
    if idx >= 0:
        rest = raw[idx + len("/assets/") :].lstrip("/")
    else:
        marker = "assets/"
        j = lower.find(marker)
        if j < 0:
            return None, None
        if j > 0 and raw[j - 1] not in ":/":
            return None, None
        rest = raw[j + len(marker) :].lstrip("/")
    parts = [p for p in rest.split("/") if p and p != ".." and not p.startswith(".")]
    if len(parts) < 2:
        return None, None
    try:
        pid = UUID(parts[0])
    except ValueError:
        return None, None
    try:
        sid = UUID(parts[1])
    except ValueError:
        return pid, None
    return pid, sid


def collect_export_attention_scene_ids(
    db: Session,
    *,
    project_id: UUID,
    tenant_id: str,
    timeline_version_id: UUID | None,
) -> list[str]:
    """
    Scene ids to highlight in the editor: no approved succeeded image/video yet, or timeline clip uses an unapproved asset.
    """
    seen: set[UUID] = set()
    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    for sc in scenes:
        assets = list(db.scalars(select(Asset).where(Asset.scene_id == sc.id)).all())
        has_appr = any(
            _is_scene_visual_asset(a) and a.status == "succeeded" and a.approved_at is not None for a in assets
        )
        if not has_appr:
            seen.add(sc.id)
    if timeline_version_id is not None:
        tv = db.get(TimelineVersion, timeline_version_id)
        if tv and tv.tenant_id == tenant_id and tv.project_id == project_id:
            tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
            clips = tj.get("clips")
            if isinstance(clips, list):
                for c in clips:
                    if not isinstance(c, dict):
                        continue
                    src = c.get("source")
                    if not isinstance(src, dict) or src.get("kind") != "asset":
                        continue
                    try:
                        aid = UUID(str(src.get("asset_id")))
                    except (ValueError, TypeError):
                        continue
                    asset = get_timeline_asset_for_project(db, aid, project_id)
                    if asset is not None and asset.approved_at is None and asset.scene_id:
                        seen.add(asset.scene_id)
    return [str(sid) for sid in sorted(seen, key=lambda u: str(u))]


def get_timeline_asset_for_project(db: Session, asset_id: UUID, project_id: UUID) -> Asset | None:
    """
    Return ``Asset`` for a timeline clip reference.

    Resolution order: scene graph join → ``Asset.project_id`` → storage URL layout
    (``…/assets/<project_id>/<scene_id>/…``) when it matches this project → **same-tenant row**
    (allows timelines to reference media from other projects in the workspace).
    """
    row = db.scalar(
        select(Asset)
        .join(Scene, Asset.scene_id == Scene.id)
        .join(Chapter, Scene.chapter_id == Chapter.id)
        .where(Asset.id == asset_id, Chapter.project_id == project_id)
    )
    if row is not None:
        return row
    loose = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.project_id == project_id))
    if loose is not None:
        return loose
    orphan = db.get(Asset, asset_id)
    if orphan is None:
        return None
    proj = db.get(Project, project_id)
    if not proj or orphan.tenant_id != proj.tenant_id:
        return None
    return orphan


def timeline_visual_asset_issue_codes(
    asset: Asset,
    *,
    storage_root: Path,
    allow_unapproved_media: bool,
) -> list[str]:
    """
    Blocker codes for a visual (image/video) timeline clip target.

    Empty list means the row is OK for export/reconcile: correct type, readable file, and either
    ``approved_at`` set (normal path) or ``allow_unapproved_media``. If **both** ``approved_at``
    and a readable file exist, the row passes **even when** ``status`` is still ``rejected`` or
    ``failed`` (stale status after Approve). Otherwise ``rejected``/``failed`` without that pair
    still blocks.

    Uses specific codes so the UI is not confused with “still generating”:
    ``timeline_clip_not_visual_asset``, ``timeline_asset_rejected_or_failed``.
    """
    at = str(asset.asset_type or "").lower()
    if at not in ("image", "video"):
        return ["timeline_clip_not_visual_asset"]

    su = asset.storage_url or ""
    lp = path_from_storage_url(su, storage_root=storage_root)
    file_ok = lp is not None and path_is_readable_file(lp)

    # Studio approval + file on disk: OK for export even if ``status`` is still ``rejected``/``failed``
    # (stale row after Approve, or worker never flipped status). Matches POST /assets/{id}/approve intent.
    if asset.approved_at is not None and file_ok:
        return []

    if asset.status in ("rejected", "failed"):
        return ["timeline_asset_rejected_or_failed"]

    if not allow_unapproved_media and asset.approved_at is None:
        return ["timeline_asset_not_approved"]

    if not file_ok:
        return ["timeline_asset_file_missing"]

    return []


def format_phase5_readiness_failure(readiness: dict[str, Any], *, label: str = "PHASE5_NOT_READY") -> str:
    """Human-readable message for jobs/logs when ``ready`` is False."""
    issues = readiness.get("issues") or []
    primary = readiness.get("primary_metric") or "unknown"
    lines = [f"{label}: {primary}"]
    for it in issues[:16]:
        code = it.get("code", "?")
        detail = it.get("detail")
        if detail:
            lines.append(f"  • {code}: {detail}")
        else:
            lines.append(f"  • {code}")
    if len(issues) > 16:
        lines.append(f"  … (+{len(issues) - 16} more)")
    return "\n".join(lines)


class Phase5GateError(ValueError):
    """Readiness/export gate failure with structured ``payload`` for ``job.result`` and APIs."""

    def __init__(self, message: str, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.payload = payload


def build_phase5_gate_payload(readiness: dict[str, Any], *, label: str) -> dict[str, Any]:
    """JSON-serializable gate payload (issue codes + optional details)."""
    raw = readiness.get("issues") or []
    issues_out: list[dict[str, Any]] = []
    for it in raw[:64]:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "unknown")
        detail = it.get("detail")
        issues_out.append({"code": code, "detail": detail})
    return {
        "code": label,
        "primary_metric": readiness.get("primary_metric"),
        "ready": readiness.get("ready"),
        "issues": issues_out,
    }


def raise_phase5_gate(readiness: dict[str, Any], *, label: str = "PHASE5_NOT_READY") -> None:
    """Raise :class:`Phase5GateError` with human ``str`` and structured ``payload``."""
    msg = format_phase5_readiness_failure(readiness, label=label)
    payload = build_phase5_gate_payload(readiness, label=label)
    raise Phase5GateError(msg, payload)


def scene_image_video_counts(db: Session, project_id: UUID) -> tuple[int, int, int, int]:
    """scenes_total, scenes_with_image, scenes_with_video, scenes_with_approved_image."""
    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    n_img = 0
    n_vid = 0
    n_appr = 0
    for sc in scenes:
        assets = list(db.scalars(select(Asset).where(Asset.scene_id == sc.id)).all())
        if any(a.asset_type == "image" and a.status == "succeeded" for a in assets):
            n_img += 1
        if any(a.asset_type == "video" and a.status == "succeeded" for a in assets):
            n_vid += 1
        if any(
            a.asset_type == "image" and a.status == "succeeded" and a.approved_at is not None for a in assets
        ):
            n_appr += 1
    return len(scenes), n_img, n_vid, n_appr


def _is_scene_visual_asset(a: Asset) -> bool:
    return str(a.asset_type or "").lower() in ("image", "video")


def scene_visual_gate_counts(db: Session, project_id: UUID) -> tuple[int, int, int]:
    """
    For export preflight scene gates: each scene should have usable **image or video** media.

    Returns ``(scene_count, scenes_with_any_succeeded_visual, scenes_with_any_approved_succeeded_visual)``.
    The latter two count a scene if **any** image/video row is ``succeeded`` (and for approval gate,
    also has ``approved_at``). Matches timelines that use scene video without a separate still.
    """
    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    n_succ = 0
    n_appr = 0
    for sc in scenes:
        assets = list(db.scalars(select(Asset).where(Asset.scene_id == sc.id)).all())
        if any(_is_scene_visual_asset(a) and a.status == "succeeded" for a in assets):
            n_succ += 1
        if any(
            _is_scene_visual_asset(a) and a.status == "succeeded" and a.approved_at is not None for a in assets
        ):
            n_appr += 1
    return len(scenes), n_succ, n_appr


def scenes_spoken_narration_coverage(db: Session, project_id: UUID) -> tuple[int, int]:
    """Scenes with ``narration_text`` vs those that have a scene-level
    ``NarrationTrack`` with audio."""
    scenes = list(
        db.scalars(
            select(Scene)
            .join(Chapter, Scene.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.order_index, Scene.order_index)
        ).all()
    )
    need = 0
    ok = 0
    for sc in scenes:
        if len((sc.narration_text or "").strip()) < 2:
            continue
        need += 1
        nt = db.scalar(
            select(NarrationTrack.id)
            .where(
                NarrationTrack.scene_id == sc.id,
                NarrationTrack.audio_url.isnot(None),
            )
            .limit(1)
        )
        if nt:
            ok += 1
    return need, ok


def chapters_narration_need_ok(db: Session, project_id: UUID) -> tuple[int, int]:
    """Chapters that need chapter-level narration vs those that have a DB row with audio_url."""
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)).all()
    )
    need = 0
    ok = 0
    for ch in chapters:
        has_script = len((ch.script_text or "").strip()) >= 12
        has_scenes = (db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch.id)) or 0) > 0
        if not has_script and not has_scenes:
            continue
        need += 1
        nt = db.scalar(
            select(NarrationTrack.id)
            .where(NarrationTrack.chapter_id == ch.id, NarrationTrack.scene_id.is_(None))
            .where(NarrationTrack.audio_url.isnot(None))
            .limit(1)
        )
        if nt:
            ok += 1
    return need, ok


def _narration_disk_issues(db: Session, project_id: UUID, storage_root: Path) -> list[dict[str, Any]]:
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)).all()
    )
    issues: list[dict[str, Any]] = []
    for ch in chapters:
        has_script = len((ch.script_text or "").strip()) >= 12
        has_scenes = (db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch.id)) or 0) > 0
        if not has_script and not has_scenes:
            continue
        row = db.scalar(
            select(NarrationTrack)
            .where(NarrationTrack.chapter_id == ch.id, NarrationTrack.scene_id.is_(None))
            .where(NarrationTrack.audio_url.isnot(None))
            .order_by(NarrationTrack.created_at.desc())
            .limit(1)
        )
        if row is None:
            continue
        url = row.audio_url or ""
        p = path_from_storage_url(url, storage_root=storage_root)
        if p is None or not path_is_readable_file(p):
            issues.append(
                {
                    "code": "narration_audio_missing_on_disk",
                    "stage": "narration",
                    "detail": {"chapter_id": str(ch.id)},
                }
            )
    return issues


def _scene_narration_disk_issues(db: Session, project_id: UUID, storage_root: Path) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(NarrationTrack)
            .where(
                NarrationTrack.project_id == project_id,
                NarrationTrack.scene_id.isnot(None),
                NarrationTrack.audio_url.isnot(None),
            )
        ).all()
    )
    issues: list[dict[str, Any]] = []
    for row in rows:
        sid = row.scene_id
        if sid is None:
            continue
        p = path_from_storage_url(row.audio_url or "", storage_root=storage_root)
        if p is None or not path_is_readable_file(p):
            issues.append(
                {
                    "code": "scene_narration_audio_missing_on_disk",
                    "stage": "narration",
                    "detail": {"scene_id": str(sid)},
                }
            )
    return issues


def _project_structural_issues(
    db: Session,
    *,
    project_id: UUID,
    storage_root: Path | None,
    export_stage: ExportStage | None = None,
    allow_unapproved_media: bool = False,
    require_scene_narration_tracks: bool = False,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    scenes_tot, scenes_img, _vid, scenes_appr_img = scene_image_video_counts(db, project_id)
    _, scenes_succ_visual, scenes_appr_visual = scene_visual_gate_counts(db, project_id)
    skip_narration_preflight = export_stage == "rough_cut"
    sn_need, sn_ok = (0, 0) if skip_narration_preflight else scenes_spoken_narration_coverage(db, project_id)

    if scenes_tot == 0:
        issues.append({"code": "no_scenes", "stage": "scenes", "detail": {}})
    elif not allow_unapproved_media and scenes_appr_visual < scenes_tot:
        issues.append(
            {
                "code": "missing_approved_scene_image",
                "stage": "images",
                "detail": {
                    "scene_count": scenes_tot,
                    "scenes_with_approved_visual": scenes_appr_visual,
                    "scenes_with_approved_image": scenes_appr_img,
                },
            },
        )
    elif allow_unapproved_media and scenes_succ_visual < scenes_tot:
        issues.append(
            {
                "code": "missing_succeeded_scene_image",
                "stage": "images",
                "detail": {
                    "scene_count": scenes_tot,
                    "scenes_with_succeeded_visual": scenes_succ_visual,
                    "scenes_with_image": scenes_img,
                },
            },
        )

    if not skip_narration_preflight:
        if require_scene_narration_tracks and sn_need > 0 and sn_ok < sn_need:
            issues.append(
                {
                    "code": "missing_scene_narration",
                    "stage": "narration",
                    "detail": {"scenes_with_audio": sn_ok, "scenes_needing_scene_vo": sn_need},
                }
            )
        if storage_root is not None:
            issues.extend(_scene_narration_disk_issues(db, project_id, storage_root))

    return issues


def _timeline_export_issues(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    export_stage: ExportStage,
    allow_unapproved_media: bool = False,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    try:
        validate_timeline_document(tj)
    except ValidationError as e:
        issues.append({"code": "invalid_timeline_json", "stage": "timeline", "detail": {"message": str(e.message)}})
        return issues

    clips = tj.get("clips")
    if not isinstance(clips, list) or len(clips) == 0:
        issues.append({"code": "timeline_empty_clips", "stage": "timeline", "detail": {}})
        return issues

    for c in clips:
        if not isinstance(c, dict):
            continue
        src = c.get("source")
        if not isinstance(src, dict) or src.get("kind") != "asset":
            issues.append({"code": "timeline_clip_not_asset", "stage": "timeline", "detail": {}})
            continue
        try:
            aid = UUID(str(src.get("asset_id")))
        except (ValueError, TypeError):
            issues.append({"code": "timeline_invalid_asset_id", "stage": "timeline", "detail": {}})
            continue
        asset = get_timeline_asset_for_project(db, aid, project.id)
        if asset is None:
            issues.append(
                {"code": "timeline_asset_not_in_project", "stage": "timeline", "detail": {"asset_id": str(aid)}}
            )
            continue
        for code in timeline_visual_asset_issue_codes(
            asset,
            storage_root=storage_root,
            allow_unapproved_media=allow_unapproved_media,
        ):
            detail: dict[str, Any] = {"asset_id": str(aid)}
            if code == "timeline_asset_rejected_or_failed":
                detail["status"] = asset.status
            elif code == "timeline_clip_not_visual_asset":
                detail["asset_type"] = asset.asset_type
            issues.append({"code": code, "stage": "timeline", "detail": detail})

    mb_ref = tj.get("music_bed_id")
    if mb_ref:
        try:
            mb_id = UUID(str(mb_ref))
        except (ValueError, TypeError):
            issues.append({"code": "invalid_music_bed_id", "stage": "timeline", "detail": {}})
        else:
            mb = db.get(MusicBed, mb_id)
            if not mb or mb.tenant_id != project.tenant_id:
                issues.append({"code": "music_bed_not_found", "stage": "timeline", "detail": {"music_bed_id": str(mb_id)}})
            else:
                lic = (mb.license_or_source_ref or "").strip()
                if not lic:
                    issues.append(
                        {
                            "code": "music_bed_missing_license",
                            "stage": "timeline",
                            "detail": {"music_bed_id": str(mb_id)},
                        }
                    )
                if mb.storage_url:
                    mp = path_from_storage_url(mb.storage_url, storage_root=storage_root)
                    if mp is None or not path_is_readable_file(mp):
                        issues.append(
                            {
                                "code": "music_bed_file_missing",
                                "stage": "timeline",
                                "detail": {"music_bed_id": str(mb_id)},
                            }
                        )

    base = storage_root / "exports" / str(project.id) / str(tv.id)
    if export_stage == "fine_cut":
        if not path_is_readable_file(base / "rough_cut.mp4"):
            issues.append(
                {
                    "code": "missing_rough_cut_for_fine",
                    "stage": "rough_cut",
                    "detail": {"timeline_version_id": str(tv.id)},
                }
            )
    if export_stage == "final_cut":
        rough_ok = path_is_readable_file(base / "rough_cut.mp4")
        fine_ok = path_is_readable_file(base / "fine_cut.mp4")
        if not rough_ok and not fine_ok:
            issues.append(
                {
                    "code": "missing_base_cut_for_final",
                    "stage": "rough_cut",
                    "detail": {"timeline_version_id": str(tv.id)},
                }
            )

    return issues


def collect_timeline_export_attention_assets(
    db: Session,
    *,
    project: Project,
    tv: TimelineVersion,
    storage_root: Path,
    allow_unapproved_media: bool = False,
) -> list[dict[str, Any]]:
    """
    Per-asset rollup for timeline export problems: ``asset_id``, optional ``scene_id`` (null if the asset
    is not linked to this project’s scene graph), ``asset_type`` when known, and ``issue_codes``.
    """
    tj = tv.timeline_json if isinstance(tv.timeline_json, dict) else {}
    try:
        validate_timeline_document(tj)
    except ValidationError:
        return []
    clips = tj.get("clips")
    if not isinstance(clips, list):
        return []

    acc: dict[UUID, dict[str, Any]] = {}

    def bump(
        aid: UUID,
        code: str,
        scene_id: UUID | None,
        asset_type: str | None,
        *,
        status: str | None = None,
    ) -> None:
        if aid not in acc:
            acc[aid] = {
                "asset_id": str(aid),
                "scene_id": str(scene_id) if scene_id else None,
                "asset_type": asset_type,
                "status": status,
                "issue_codes": [],
            }
        e = acc[aid]
        if code not in e["issue_codes"]:
            e["issue_codes"].append(code)
        if scene_id:
            e["scene_id"] = str(scene_id)
        if asset_type and not e.get("asset_type"):
            e["asset_type"] = asset_type
        if status:
            e["status"] = status

    for c in clips:
        if not isinstance(c, dict):
            continue
        src = c.get("source")
        if not isinstance(src, dict) or src.get("kind") != "asset":
            continue
        try:
            aid = UUID(str(src.get("asset_id")))
        except (ValueError, TypeError):
            continue
        asset = get_timeline_asset_for_project(db, aid, project.id)
        if asset is None:
            bump(aid, "timeline_asset_not_in_project", None, None)
            continue
        sid = asset.scene_id
        at = str(asset.asset_type) if asset.asset_type else None
        st = str(asset.status) if asset.status else None
        for code in timeline_visual_asset_issue_codes(
            asset,
            storage_root=storage_root,
            allow_unapproved_media=allow_unapproved_media,
        ):
            bump(aid, code, sid, at, status=st)

    out = list(acc.values())
    out.sort(key=lambda x: str(x["asset_id"]))
    for e in out:
        e["issue_codes"] = sorted(e["issue_codes"])
    return out


def compute_phase5_readiness(
    db: Session,
    *,
    project_id: UUID,
    tenant_id: str,
    timeline_version_id: UUID | None = None,
    storage_root: str | Path | None = None,
    export_stage: ExportStage | None = None,
    allow_unapproved_media: bool = False,
    require_scene_narration_tracks: bool = False,
) -> dict[str, Any]:
    """
    Deterministic preflight for Phase 5 exports.

    - Default (no ``export_stage``): project-level structural checks; optional ``storage_root``
      adds narration file-on-disk verification.
    - ``export_stage`` ``rough_cut`` / ``fine_cut`` / ``final_cut``: requires ``timeline_version_id`` and
      ``storage_root``; validates timeline clips, assets on disk, music bed contract; ``fine_cut``
      requires ``rough_cut.mp4``; ``final_cut`` requires ``rough_cut.mp4`` or ``fine_cut.mp4``.
    - ``allow_unapproved_media``: relax approval gates (Hands-off / unattended). Scene gate requires a
      succeeded **image or video** per scene (not only stills). Timeline clips still need readable files.
    - ``require_scene_narration_tracks``: when True, block if scenes with ``narration_text`` lack scene TTS.
      Default False — missing VO is mixed as silence for the clip duration (final mux).
    """
    p = db.get(Project, project_id)
    if not p or p.tenant_id != tenant_id:
        return {
            "ready": False,
            "error": "project_not_found",
            "issues": [],
            "primary_metric": None,
            "export_attention_scene_ids": [],
            "export_attention_timeline_assets": [],
        }

    root: Path | None = Path(storage_root).resolve() if storage_root is not None else None

    issues = _project_structural_issues(
        db,
        project_id=project_id,
        storage_root=root,
        export_stage=export_stage,
        allow_unapproved_media=allow_unapproved_media,
        require_scene_narration_tracks=require_scene_narration_tracks,
    )

    timeline_attention_assets: list[dict[str, Any]] = []

    if export_stage is not None:
        if timeline_version_id is None or root is None:
            issues.append(
                {
                    "code": "export_preflight_missing_context",
                    "stage": "timeline",
                    "detail": {"export_stage": export_stage, "need_timeline_version_id": True, "need_storage_root": True},
                }
            )
        else:
            tv = db.get(TimelineVersion, timeline_version_id)
            if not tv or tv.tenant_id != tenant_id or tv.project_id != project_id:
                issues.append(
                    {
                        "code": "timeline_version_not_found",
                        "stage": "timeline",
                        "detail": {"timeline_version_id": str(timeline_version_id)},
                    }
                )
            else:
                issues.extend(
                    _timeline_export_issues(
                        db,
                        project=p,
                        tv=tv,
                        storage_root=root,
                        export_stage=export_stage,
                        allow_unapproved_media=allow_unapproved_media,
                    )
                )
                timeline_attention_assets = collect_timeline_export_attention_assets(
                    db,
                    project=p,
                    tv=tv,
                    storage_root=root,
                    allow_unapproved_media=allow_unapproved_media,
                )

    ready = len(issues) == 0
    scenes_tot, scenes_img, scenes_vid, scenes_appr = scene_image_video_counts(db, project_id)
    _, scenes_succ_visual, scenes_appr_visual = scene_visual_gate_counts(db, project_id)
    sn_need, sn_ok = scenes_spoken_narration_coverage(db, project_id)
    primary = "export_preflight_ok" if ready else (issues[0].get("code") if issues else "unknown")

    attention = collect_export_attention_scene_ids(
        db,
        project_id=project_id,
        tenant_id=tenant_id,
        timeline_version_id=timeline_version_id,
    )

    return {
        "ready": ready,
        "issues": issues,
        "primary_metric": primary,
        "export_attention_scene_ids": attention,
        "export_attention_timeline_assets": timeline_attention_assets,
        "allow_unapproved_media": allow_unapproved_media,
        "project_id": str(project_id),
        "scene_count": scenes_tot,
        "scenes_with_image": scenes_img,
        "scenes_with_video": scenes_vid,
        "scenes_with_approved_image": scenes_appr,
        "scenes_with_succeeded_visual": scenes_succ_visual,
        "scenes_with_approved_visual": scenes_appr_visual,
        "scenes_needing_narration": sn_need,
        "scenes_with_narration_audio": sn_ok,
        "require_scene_narration_tracks": require_scene_narration_tracks,
    }
