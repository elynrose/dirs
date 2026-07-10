"""Tests for cancelling queued scene precompile jobs."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from director_api.db.models import Asset, Job, TimelineVersion
from director_api.services import scene_precompile_enqueue as spe


def _queued_precompile_job(
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> Job:
    return Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=project_id,
        type="scene_precompile",
        status="queued",
        payload={"asset_id": str(asset_id), "project_id": str(project_id)},
    )


def test_cancel_all_queued_scene_precompiles() -> None:
    tenant = "tenant-a"
    pid = uuid.uuid4()
    jobs = [
        _queued_precompile_job(tenant_id=tenant, project_id=pid, asset_id=uuid.uuid4()),
        _queued_precompile_job(tenant_id=tenant, project_id=pid, asset_id=uuid.uuid4()),
    ]
    db = MagicMock()
    db.scalars.return_value.all.return_value = jobs

    with patch("director_api.tasks.celery_app.celery_app") as celery:
        n = spe.cancel_queued_scene_precompiles(
            db,
            tenant_id=tenant,
            project_id=pid,
            reason="test_cancel_all",
        )
    assert n == 2
    assert all(j.status == "cancelled" for j in jobs)
    assert celery.control.revoke.call_count == 2


def test_cancel_keeps_timeline_assets_only() -> None:
    tenant = "tenant-a"
    pid = uuid.uuid4()
    keep_id = uuid.uuid4()
    drop_id = uuid.uuid4()
    keep_job = _queued_precompile_job(tenant_id=tenant, project_id=pid, asset_id=keep_id)
    drop_job = _queued_precompile_job(tenant_id=tenant, project_id=pid, asset_id=drop_id)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [keep_job, drop_job]

    with patch("director_api.tasks.celery_app.celery_app"):
        n = spe.cancel_queued_scene_precompiles(
            db,
            tenant_id=tenant,
            project_id=pid,
            reason="cancelled_not_on_timeline",
            asset_ids_keep={keep_id},
        )
    assert n == 1
    assert keep_job.status == "queued"
    assert drop_job.status == "cancelled"


def test_schedule_scene_precompile_if_on_timeline_skips_off_timeline() -> None:
    tenant = "tenant-a"
    pid = uuid.uuid4()
    asset_id = uuid.uuid4()
    asset = Asset(
        id=asset_id,
        tenant_id=tenant,
        project_id=pid,
        status="succeeded",
        asset_type="image",
        storage_url="file:///x.png",
    )
    tv = TimelineVersion(
        id=uuid.uuid4(),
        tenant_id=tenant,
        project_id=pid,
        version_name="v1",
        timeline_json={"clips": []},
    )
    db = MagicMock()
    db.scalars.return_value.first.return_value = tv

    with patch.object(spe, "schedule_scene_precompile_for_asset") as sched:
        out = spe.schedule_scene_precompile_if_on_timeline(db, MagicMock(), asset)
    assert out is None
    sched.assert_not_called()


def test_timeline_asset_ids_from_clips() -> None:
    aid = uuid.uuid4()
    tj = {
        "clips": [
            {"source": {"kind": "asset", "asset_id": str(aid)}},
            {"source": {"kind": "music"}},
        ]
    }
    assert spe.timeline_asset_ids(tj) == {aid}
