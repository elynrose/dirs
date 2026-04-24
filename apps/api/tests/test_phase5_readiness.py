"""Phase 5 readiness: deterministic structural preflight (no critic gate)."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from director_api.services.phase5_readiness import (
    Phase5GateError,
    build_phase5_gate_payload,
    compute_phase5_readiness,
    format_phase5_readiness_failure,
    get_timeline_asset_for_project,
    parse_assets_layout_project_scene,
    raise_phase5_gate,
    timeline_visual_asset_issue_codes,
    _project_structural_issues,
)


def test_phase5_ready_when_project_exists():
    pid = uuid4()
    tenant = "t1"
    proj = MagicMock()
    proj.tenant_id = tenant
    proj.id = pid
    db = MagicMock()
    db.get.return_value = proj
    with (
        patch("director_api.services.phase5_readiness.scene_image_video_counts", return_value=(1, 1, 0, 1)),
        patch("director_api.services.phase5_readiness.scene_visual_gate_counts", return_value=(1, 1, 1)),
        patch("director_api.services.phase5_readiness.scenes_spoken_narration_coverage", return_value=(0, 0)),
    ):
        r = compute_phase5_readiness(db, project_id=pid, tenant_id=tenant)
    assert r["ready"] is True
    assert r["issues"] == []
    assert r["primary_metric"] == "export_preflight_ok"
    assert r.get("export_attention_timeline_assets") == []


def test_phase5_not_ready_wrong_tenant():
    pid = uuid4()
    db = MagicMock()
    db.get.return_value = None
    r = compute_phase5_readiness(db, project_id=pid, tenant_id="t1")
    assert r["ready"] is False
    assert r.get("error") == "project_not_found"


def test_format_phase5_readiness_failure_lists_issues():
    r = {
        "primary_metric": "missing_approved_scene_image",
        "issues": [{"code": "missing_approved_scene_image", "detail": {"scene_count": 3}}],
    }
    msg = format_phase5_readiness_failure(r)
    assert "PHASE5_NOT_READY" in msg
    assert "missing_approved_scene_image" in msg
    assert "scene_count" in msg


def test_raise_phase5_gate_structured_payload():
    r = {
        "ready": False,
        "primary_metric": "missing_approved_scene_image",
        "issues": [{"code": "missing_approved_scene_image", "detail": {"scene_count": 2}}],
    }
    with pytest.raises(Phase5GateError) as exc_info:
        raise_phase5_gate(r, label="PHASE5_NOT_READY")
    err = exc_info.value
    assert err.payload["code"] == "PHASE5_NOT_READY"
    assert err.payload["issues"][0]["code"] == "missing_approved_scene_image"
    assert "PHASE5_NOT_READY" in str(err)


def test_build_phase5_gate_payload_truncates_issues():
    r = {
        "primary_metric": "x",
        "issues": [{"code": f"c{i}"} for i in range(100)],
    }
    p = build_phase5_gate_payload(r, label="PHASE5_NOT_READY")
    assert len(p["issues"]) == 64


def test_structural_issues_scene_narration_required_when_opt_in():
    """With require_scene_narration_tracks=True, spoken scenes without scene TTS are blocked."""
    pid = uuid4()
    db = MagicMock()
    with (
        patch("director_api.services.phase5_readiness.scene_image_video_counts", return_value=(2, 2, 0, 2)),
        patch("director_api.services.phase5_readiness.scene_visual_gate_counts", return_value=(2, 2, 2)),
        patch(
            "director_api.services.phase5_readiness.scenes_spoken_narration_coverage",
            return_value=(3, 0),
        ),
        patch("director_api.services.phase5_readiness._scene_narration_disk_issues", return_value=[]),
    ):
        issues = _project_structural_issues(
            db,
            project_id=pid,
            storage_root=MagicMock(),
            export_stage="final_cut",
            require_scene_narration_tracks=True,
        )
    assert any(i.get("code") == "missing_scene_narration" for i in issues)


def test_structural_issues_missing_scene_narration_ok_when_optional():
    """Default: scenes can have narration_text but no TTS — export uses silence for those beats."""
    pid = uuid4()
    db = MagicMock()
    with (
        patch("director_api.services.phase5_readiness.scene_image_video_counts", return_value=(2, 2, 0, 2)),
        patch("director_api.services.phase5_readiness.scene_visual_gate_counts", return_value=(2, 2, 2)),
        patch(
            "director_api.services.phase5_readiness.scenes_spoken_narration_coverage",
            return_value=(3, 0),
        ),
        patch("director_api.services.phase5_readiness._scene_narration_disk_issues", return_value=[]),
    ):
        issues = _project_structural_issues(
            db,
            project_id=pid,
            storage_root=MagicMock(),
            export_stage="final_cut",
            require_scene_narration_tracks=False,
        )
    assert not any(i.get("code") == "missing_scene_narration" for i in issues)


def test_rough_cut_structural_skips_narration_checks():
    pid = uuid4()
    db = MagicMock()
    with (
        patch("director_api.services.phase5_readiness.scene_image_video_counts", return_value=(2, 2, 0, 2)),
        patch("director_api.services.phase5_readiness.scene_visual_gate_counts", return_value=(2, 2, 2)),
        patch("director_api.services.phase5_readiness.scenes_spoken_narration_coverage") as sn,
    ):
        issues = _project_structural_issues(
            db, project_id=pid, storage_root=MagicMock(), export_stage="rough_cut"
        )
    sn.assert_not_called()
    assert issues == []


def test_phase5_not_ready_no_scenes():
    pid = uuid4()
    tenant = "t1"
    proj = MagicMock()
    proj.tenant_id = tenant
    proj.id = pid
    db = MagicMock()
    db.get.return_value = proj
    with (
        patch("director_api.services.phase5_readiness.scene_image_video_counts", return_value=(0, 0, 0, 0)),
        patch("director_api.services.phase5_readiness.scene_visual_gate_counts", return_value=(0, 0, 0)),
        patch("director_api.services.phase5_readiness.scenes_spoken_narration_coverage", return_value=(0, 0)),
    ):
        r = compute_phase5_readiness(db, project_id=pid, tenant_id=tenant)
    assert r["ready"] is False
    assert any(i.get("code") == "no_scenes" for i in r["issues"])


def test_structural_gate_approved_video_satisfies_strict_without_scene_image():
    """Scene-level strict gate counts approved succeeded video, not only stills."""
    pid = uuid4()
    db = MagicMock()
    with (
        patch("director_api.services.phase5_readiness.scene_image_video_counts", return_value=(1, 0, 1, 0)),
        patch("director_api.services.phase5_readiness.scene_visual_gate_counts", return_value=(1, 1, 1)),
        patch("director_api.services.phase5_readiness.scenes_spoken_narration_coverage", return_value=(0, 0)),
    ):
        issues = _project_structural_issues(
            db,
            project_id=pid,
            storage_root=MagicMock(),
            export_stage="rough_cut",
            allow_unapproved_media=False,
        )
    assert not any(i.get("code") == "missing_approved_scene_image" for i in issues)


def test_allow_unapproved_media_skips_approval_gate():
    """Hands-off: succeeded visuals (image or video) enough; do not require approved_at on every scene."""
    pid = uuid4()
    tenant = "t1"
    proj = MagicMock()
    proj.tenant_id = tenant
    proj.id = pid
    db = MagicMock()
    db.get.return_value = proj
    with (
        patch("director_api.services.phase5_readiness.scene_image_video_counts", return_value=(2, 2, 0, 0)),
        patch("director_api.services.phase5_readiness.scene_visual_gate_counts", return_value=(2, 2, 0)),
        patch("director_api.services.phase5_readiness.scenes_spoken_narration_coverage", return_value=(0, 0)),
        patch("director_api.services.phase5_readiness.collect_export_attention_scene_ids", return_value=[]),
    ):
        r = compute_phase5_readiness(
            db, project_id=pid, tenant_id=tenant, allow_unapproved_media=True
        )
    assert r["allow_unapproved_media"] is True
    assert not any(i.get("code") == "missing_approved_scene_image" for i in r["issues"])


def test_allow_unapproved_false_still_blocks_unapproved_images():
    pid = uuid4()
    tenant = "t1"
    proj = MagicMock()
    proj.tenant_id = tenant
    proj.id = pid
    db = MagicMock()
    db.get.return_value = proj
    with (
        patch("director_api.services.phase5_readiness.scene_image_video_counts", return_value=(2, 2, 0, 0)),
        patch("director_api.services.phase5_readiness.scene_visual_gate_counts", return_value=(2, 2, 0)),
        patch("director_api.services.phase5_readiness.scenes_spoken_narration_coverage", return_value=(0, 0)),
        patch("director_api.services.phase5_readiness.collect_export_attention_scene_ids", return_value=[]),
    ):
        r = compute_phase5_readiness(db, project_id=pid, tenant_id=tenant, allow_unapproved_media=False)
    assert r["allow_unapproved_media"] is False
    assert any(i.get("code") == "missing_approved_scene_image" for i in r["issues"])


def test_get_timeline_asset_for_project_falls_back_when_scene_graph_missing():
    pid = uuid4()
    aid = uuid4()
    loose = MagicMock()
    db = MagicMock()
    db.scalar.side_effect = [None, loose]
    assert get_timeline_asset_for_project(db, aid, pid) is loose
    assert db.scalar.call_count == 2


def test_parse_assets_layout_project_scene_file_url():
    pid = uuid4()
    sid = uuid4()
    url = f"file:///D:/data/storage/assets/{pid}/{sid}/44050c3d-7cec-476a-b118-419d82a718c0.jpg"
    pp, ss = parse_assets_layout_project_scene(url)
    assert pp == pid
    assert ss == sid


def test_get_timeline_asset_for_project_resolves_from_storage_url_layout():
    pid = uuid4()
    sid = uuid4()
    aid = uuid4()
    url = f"file:///D:/storage/assets/{pid}/{sid}/x.jpg"
    orphan = MagicMock()
    orphan.tenant_id = "t1"
    orphan.storage_url = url
    proj = MagicMock()
    proj.tenant_id = "t1"
    db = MagicMock()
    db.scalar.side_effect = [None, None]
    db.get.side_effect = [orphan, proj]
    assert get_timeline_asset_for_project(db, aid, pid) is orphan


def test_get_timeline_asset_for_project_same_tenant_even_if_other_project_storage_path():
    """Timeline may reference an asset row from another project if tenant matches."""
    pid = uuid4()
    other_pid = uuid4()
    aid = uuid4()
    orphan = MagicMock()
    orphan.tenant_id = "t1"
    orphan.storage_url = f"file:///D:/storage/assets/{other_pid}/{uuid4()}/x.jpg"
    proj = MagicMock()
    proj.tenant_id = "t1"
    db = MagicMock()
    db.scalar.side_effect = [None, None]
    db.get.side_effect = [orphan, proj]
    assert get_timeline_asset_for_project(db, aid, pid) is orphan


def test_get_timeline_asset_for_project_rejects_other_tenant():
    pid = uuid4()
    aid = uuid4()
    orphan = MagicMock()
    orphan.tenant_id = "tenant-b"
    proj = MagicMock()
    proj.tenant_id = "tenant-a"
    db = MagicMock()
    db.scalar.side_effect = [None, None]
    db.get.side_effect = [orphan, proj]
    assert get_timeline_asset_for_project(db, aid, pid) is None


def test_timeline_visual_asset_running_with_file_is_export_ready(tmp_path: Path):
    """Non-succeeded DB status is OK when the file exists and the row is approved."""
    f = tmp_path / "x.jpg"
    f.write_bytes(b"x")
    uri = f.resolve().as_uri()
    a = SimpleNamespace(
        asset_type="image",
        status="running",
        approved_at=datetime.now(timezone.utc),
        storage_url=uri,
    )
    assert timeline_visual_asset_issue_codes(
        a, storage_root=tmp_path, allow_unapproved_media=False
    ) == []


def test_timeline_visual_asset_wrong_type_code(tmp_path: Path):
    a = SimpleNamespace(
        asset_type="audio",
        status="succeeded",
        approved_at=datetime.now(timezone.utc),
        storage_url="",
    )
    assert timeline_visual_asset_issue_codes(a, storage_root=tmp_path, allow_unapproved_media=False) == [
        "timeline_clip_not_visual_asset"
    ]


def test_timeline_visual_asset_failed_status_code(tmp_path: Path):
    a = SimpleNamespace(
        asset_type="image",
        status="failed",
        approved_at=datetime.now(timezone.utc),
        storage_url="",
    )
    assert timeline_visual_asset_issue_codes(a, storage_root=tmp_path, allow_unapproved_media=False) == [
        "timeline_asset_rejected_or_failed"
    ]


def test_timeline_visual_asset_rejected_with_approval_and_file_is_export_ready(tmp_path: Path):
    """Approve + file overrides stale ``status=rejected`` (same intent as POST /assets/…/approve)."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"fake")
    uri = f.resolve().as_uri()
    a = SimpleNamespace(
        asset_type="video",
        status="rejected",
        approved_at=datetime.now(timezone.utc),
        storage_url=uri,
    )
    assert timeline_visual_asset_issue_codes(
        a, storage_root=tmp_path, allow_unapproved_media=False
    ) == []
