"""Timeline image repair helpers (rough-cut preflight)."""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from director_api.services.timeline_image_repair import (
    auto_approve_timeline_clip_assets,
    clip_visual_needs_replacement,
    filter_flagged_timeline_image_rows,
    pick_replacement_visual_for_timeline_clip,
    rebind_orphan_timeline_clips_by_scene_order,
    reconcile_timeline_clip_images,
)


def test_rebind_orphan_timeline_clips_by_scene_order(monkeypatch):
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    bad_id = uuid.uuid4()
    good_id = uuid.uuid4()
    scene = SimpleNamespace(id=sid)
    proj = SimpleNamespace(id=pid, tenant_id="t")
    tv = SimpleNamespace(
        timeline_json={
            "schema_version": 1,
            "clips": [{"order_index": 0, "source": {"kind": "asset", "asset_id": str(bad_id)}}],
        }
    )
    good_asset = SimpleNamespace(id=good_id)

    def _gt(db, aid, p):
        return None if aid == bad_id else good_asset

    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.get_timeline_asset_for_project",
        _gt,
    )
    def _pick(_db, **kwargs):
        return good_asset

    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.pick_primary_export_asset_for_scene",
        _pick,
    )
    db = MagicMock()
    scal_out = MagicMock()
    scal_out.all.return_value = [scene]
    db.scalars.return_value = scal_out
    n = rebind_orphan_timeline_clips_by_scene_order(
        db,
        project=proj,
        tv=tv,
        storage_root=Path("/tmp"),
        allow_unapproved_media=False,
    )
    assert n == 1
    assert tv.timeline_json["clips"][0]["source"]["asset_id"] == str(good_id)


def test_rebind_orphan_falls_back_when_strict_primary_missing(monkeypatch):
    """Orphan rebound tries allow_unapproved_media=True if strict pick finds nothing on disk."""
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    bad_id = uuid.uuid4()
    good_id = uuid.uuid4()
    scene = SimpleNamespace(id=sid)
    proj = SimpleNamespace(id=pid, tenant_id="t")
    tv = SimpleNamespace(
        timeline_json={
            "schema_version": 1,
            "clips": [{"order_index": 0, "source": {"kind": "asset", "asset_id": str(bad_id)}}],
        }
    )
    good_asset = SimpleNamespace(id=good_id)
    calls: list[bool] = []

    def _gt(db, aid, p):
        return None if aid == bad_id else good_asset

    def _pick(_db, **kwargs):
        calls.append(bool(kwargs.get("allow_unapproved_media")))
        if kwargs.get("allow_unapproved_media"):
            return good_asset
        return None

    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.get_timeline_asset_for_project",
        _gt,
    )
    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.pick_primary_export_asset_for_scene",
        _pick,
    )
    db = MagicMock()
    scal_out = MagicMock()
    scal_out.all.return_value = [scene]
    db.scalars.return_value = scal_out
    n = rebind_orphan_timeline_clips_by_scene_order(
        db,
        project=proj,
        tv=tv,
        storage_root=Path("/tmp"),
        allow_unapproved_media=False,
    )
    assert calls == [False, True]
    assert n == 1
    assert tv.timeline_json["clips"][0]["source"]["asset_id"] == str(good_id)


def test_rebind_orphan_uses_db_only_when_disk_pick_always_fails(monkeypatch):
    """If local path resolution never finds files, still rebind to a DB succeeded asset."""
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    bad_id = uuid.uuid4()
    good_id = uuid.uuid4()
    scene = SimpleNamespace(id=sid)
    proj = SimpleNamespace(id=pid, tenant_id="t")
    tv = SimpleNamespace(
        timeline_json={
            "schema_version": 1,
            "clips": [{"order_index": 0, "source": {"kind": "asset", "asset_id": str(bad_id)}}],
        }
    )
    good_asset = SimpleNamespace(id=good_id)

    def _gt(db, aid, p):
        return None if aid == bad_id else good_asset

    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.get_timeline_asset_for_project",
        _gt,
    )
    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.pick_primary_export_asset_for_scene",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.pick_scene_export_asset_db_only",
        lambda *_a, **_k: good_asset,
    )
    db = MagicMock()
    scal_out = MagicMock()
    scal_out.all.return_value = [scene]
    db.scalars.return_value = scal_out
    n = rebind_orphan_timeline_clips_by_scene_order(
        db,
        project=proj,
        tv=tv,
        storage_root=Path("/tmp"),
        allow_unapproved_media=False,
    )
    assert n == 1
    assert tv.timeline_json["clips"][0]["source"]["asset_id"] == str(good_id)


def test_auto_approve_timeline_clip_assets_empty_timeline():
    tv = SimpleNamespace(timeline_json={"schema_version": 1, "clips": []})
    project = SimpleNamespace(id=uuid.uuid4(), tenant_id="t")
    n = auto_approve_timeline_clip_assets(MagicMock(), project=project, tv=tv, storage_root=Path("/tmp"))
    assert n == 0


def test_filter_flagged_keeps_only_repairable_images():
    sid = str(uuid.uuid4())
    rows = [
        {
            "asset_id": str(uuid.uuid4()),
            "scene_id": sid,
            "asset_type": "image",
            "issue_codes": ["timeline_asset_not_approved"],
        },
        {
            "asset_id": str(uuid.uuid4()),
            "scene_id": None,
            "asset_type": "image",
            "issue_codes": ["timeline_asset_not_in_project"],
        },
        {
            "asset_id": str(uuid.uuid4()),
            "scene_id": sid,
            "asset_type": "video",
            "issue_codes": ["timeline_asset_not_approved"],
        },
    ]
    out = filter_flagged_timeline_image_rows(rows)
    assert len(out) == 1
    assert out[0]["asset_type"] == "image"
    assert out[0]["scene_id"] == sid


def test_clip_visual_needs_replacement_image_rejected():
    a = SimpleNamespace(
        asset_type="image",
        status="rejected",
        approved_at=None,
        storage_url="",
    )
    assert clip_visual_needs_replacement(a, storage_root=Path("/tmp"), allow_unapproved_media=False) is True


def test_clip_visual_needs_replacement_image_unapproved_strict(tmp_path):
    f = tmp_path / "x.jpg"
    f.write_bytes(b"1")
    uri = f.resolve().as_uri()
    a = SimpleNamespace(
        asset_type="image",
        status="succeeded",
        approved_at=None,
        storage_url=uri,
    )
    root = tmp_path
    assert clip_visual_needs_replacement(a, storage_root=root, allow_unapproved_media=False) is True
    assert clip_visual_needs_replacement(a, storage_root=root, allow_unapproved_media=True) is False


def test_reconcile_swaps_rejected_clip_to_replacement(monkeypatch):
    tenant = "00000000-0000-0000-0000-000000000001"
    project_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    bad_id = uuid.uuid4()
    good_id = uuid.uuid4()

    bad_asset = SimpleNamespace(
        id=bad_id,
        scene_id=scene_id,
        asset_type="image",
        status="rejected",
        approved_at=None,
        storage_url="",
    )

    def fake_get(_db, aid, pid):
        if aid == bad_id and pid == project_id:
            return bad_asset
        return None

    def fake_pick_rep(_db, **kwargs):
        assert bad_id in (kwargs.get("exclude_asset_ids") or set())
        return SimpleNamespace(id=good_id)

    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.get_timeline_asset_for_project",
        fake_get,
    )
    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.pick_replacement_visual_for_timeline_clip",
        fake_pick_rep,
    )

    tj = {
        "schema_version": 1,
        "clips": [
            {
                "order_index": 0,
                "source": {"kind": "asset", "asset_id": str(bad_id)},
            }
        ],
    }
    tv = SimpleNamespace(timeline_json=dict(tj))
    project = SimpleNamespace(id=project_id, tenant_id=tenant)

    updated, unchanged = reconcile_timeline_clip_images(
        MagicMock(),
        project=project,
        tv=tv,
        storage_root=Path("/tmp"),
        allow_unapproved_media=False,
    )
    assert updated == 1
    assert unchanged == 0
    assert tv.timeline_json["clips"][0]["source"]["asset_id"] == str(good_id)


def test_reconcile_swaps_rejected_video_clip(monkeypatch):
    tenant = "00000000-0000-0000-0000-000000000001"
    project_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    bad_id = uuid.uuid4()
    good_id = uuid.uuid4()

    bad_asset = SimpleNamespace(
        id=bad_id,
        scene_id=scene_id,
        asset_type="video",
        status="rejected",
        approved_at=None,
        storage_url="",
    )

    def fake_get(_db, aid, pid):
        if aid == bad_id and pid == project_id:
            return bad_asset
        return None

    def fake_pick_rep(_db, **kwargs):
        assert bad_id in (kwargs.get("exclude_asset_ids") or set())
        return SimpleNamespace(id=good_id)

    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.get_timeline_asset_for_project",
        fake_get,
    )
    monkeypatch.setattr(
        "director_api.services.timeline_image_repair.pick_replacement_visual_for_timeline_clip",
        fake_pick_rep,
    )

    tj = {
        "schema_version": 1,
        "clips": [
            {
                "order_index": 0,
                "source": {"kind": "asset", "asset_id": str(bad_id)},
            }
        ],
    }
    tv = SimpleNamespace(timeline_json=dict(tj))
    project = SimpleNamespace(id=project_id, tenant_id=tenant)

    updated, unchanged = reconcile_timeline_clip_images(
        MagicMock(),
        project=project,
        tv=tv,
        storage_root=Path("/tmp"),
        allow_unapproved_media=False,
    )
    assert updated == 1
    assert tv.timeline_json["clips"][0]["source"]["asset_id"] == str(good_id)


def test_clip_visual_needs_replacement_video_rejected():
    a = SimpleNamespace(
        asset_type="video",
        status="rejected",
        approved_at=None,
        storage_url="",
    )
    assert clip_visual_needs_replacement(a, storage_root=Path("/tmp"), allow_unapproved_media=False) is True


def test_clip_visual_needs_replacement_non_visual_so_reconcile_can_fix():
    """Preflight uses timeline_clip_not_visual_asset for non image/video refs; reconcile must not skip them."""
    a = SimpleNamespace(
        asset_type="audio",
        status="succeeded",
        approved_at=None,
        storage_url="",
    )
    assert clip_visual_needs_replacement(a, storage_root=Path("/tmp"), allow_unapproved_media=False) is True


def test_pick_replacement_includes_stale_rejected_with_approval_and_file(tmp_path):
    """
    SQL must not exclude status=rejected when approved_at + readable file pass preflight;
    otherwise reconcile cannot point the clip at that row.
    """
    f = tmp_path / "stale.jpg"
    f.write_bytes(b"1")
    uri = f.resolve().as_uri()
    scene_id = uuid.uuid4()
    project_id = uuid.uuid4()
    bad_id = uuid.uuid4()
    good_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    stale = SimpleNamespace(
        id=good_id,
        scene_id=scene_id,
        asset_type="image",
        status="rejected",
        approved_at=now,
        storage_url=uri,
        timeline_sequence=1,
        created_at=now,
    )
    results = [[], [stale]]
    call_i = [0]

    def fake_scalars(_stmt):
        m = MagicMock()
        i = call_i[0]
        call_i[0] += 1
        m.all.return_value = results[i] if i < len(results) else []
        return m

    db = MagicMock()
    db.scalars.side_effect = fake_scalars
    project = SimpleNamespace(id=project_id, tenant_id="t1")
    picked = pick_replacement_visual_for_timeline_clip(
        db,
        scene_id=scene_id,
        project=project,
        storage_root=tmp_path,
        allow_unapproved_media=False,
        exclude_asset_ids={bad_id},
    )
    assert picked is not None and picked.id == good_id
