"""Multi-tenant: readiness must not leak another workspace's project."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from director_api.services.phase5_readiness import compute_phase5_readiness

pytestmark = pytest.mark.stress


def test_readiness_wrong_tenant_is_project_not_found() -> None:
    pid = uuid4()
    proj = MagicMock()
    proj.tenant_id = "tenant-workspace-a"
    db = MagicMock()
    db.get.return_value = proj

    r = compute_phase5_readiness(db, project_id=pid, tenant_id="tenant-workspace-b")
    assert r.get("error") == "project_not_found"
    assert r.get("ready") is False
