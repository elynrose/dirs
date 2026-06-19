"""Cross-tenant isolation checks (CI integration suite)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from director_api.db.models import Job
from director_api.services.phase5_readiness import compute_phase5_readiness
from director_api.storage.filesystem import FilesystemStorage, resolve_storage_path
from director_api.tasks.worker_helpers import worker_tenant_id


def test_worker_tenant_id_prefers_payload_then_job() -> None:
    job = Job(id=uuid.uuid4(), tenant_id="tenant-a", type="test", status="queued", payload={})
    assert worker_tenant_id(job, {"tenant_id": "tenant-b"}) == "tenant-b"
    assert worker_tenant_id(job) == "tenant-a"


def test_worker_tenant_id_raises_when_missing() -> None:
    job = Job(id=uuid.uuid4(), tenant_id="", type="test", status="queued", payload={})
    with pytest.raises(ValueError, match="missing tenant_id"):
        worker_tenant_id(job)


def test_readiness_wrong_tenant_is_project_not_found() -> None:
    pid = uuid.uuid4()
    proj = MagicMock()
    proj.tenant_id = "tenant-workspace-a"
    db = MagicMock()
    db.get.return_value = proj

    r = compute_phase5_readiness(db, project_id=pid, tenant_id="tenant-workspace-b")
    assert r.get("error") == "project_not_found"
    assert r.get("ready") is False


def test_resolve_storage_path_dual_read_legacy_and_tenant_scoped(tmp_path: Path) -> None:
    tenant = "00000000-0000-0000-0000-000000000002"
    project = "11111111-1111-1111-1111-111111111111"
    legacy_key = f"assets/{project}/scene/still.jpg"
    tenant_key = f"assets/{tenant}/{project}/scene/still.jpg"

    storage = FilesystemStorage(str(tmp_path))
    legacy_path = storage.get_path(legacy_key)
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b"legacy")

    assert resolve_storage_path(storage, legacy_key, tenant_id=tenant) == legacy_path

    legacy_path.unlink()
    tenant_path = storage.get_path(tenant_key)
    tenant_path.parent.mkdir(parents=True, exist_ok=True)
    tenant_path.write_bytes(b"tenant")

    assert resolve_storage_path(storage, legacy_key, tenant_id=tenant) == tenant_path
    assert resolve_storage_path(storage, tenant_key, tenant_id=tenant) == tenant_path
