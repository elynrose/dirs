"""Pipeline status aggregation for Studio inspector (character bible row + ordering)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from director_api.services.pipeline_status import compute_pipeline_status


@patch("director_api.services.pipeline_status.compute_phase5_readiness")
@patch("director_api.services.pipeline_status.scenes_spoken_narration_coverage")
@patch("director_api.services.pipeline_status.scene_image_video_counts")
def test_compute_pipeline_status_characters_row_after_scenes_pending_when_empty(
    mock_scene_counts: MagicMock,
    mock_narr_cov: MagicMock,
    mock_p5: MagicMock,
) -> None:
    pid = uuid4()
    tenant = "t1"
    proj = MagicMock()
    proj.tenant_id = tenant
    proj.id = pid
    proj.workflow_phase = "scenes_planned"
    proj.director_output_json = {"ok": True}

    db = MagicMock()
    db.get.return_value = proj
    mock_scene_counts.return_value = (2, 0, 0, 0)
    mock_narr_cov.return_value = (0, 0)
    mock_p5.return_value = {"ready": False, "issues": []}
    db.scalar.side_effect = [1, 1, 0, 0]
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    db.scalars.return_value = scalars_result

    out = compute_pipeline_status(db, project_id=pid, tenant_id=tenant, storage_root=None)
    assert out["ok"] is True
    steps = out["steps"]
    ids = [s["id"] for s in steps]
    assert ids.index("story_research_review") == ids.index("scenes") + 1
    assert ids.index("characters") == ids.index("story_research_review") + 1
    assert ids.index("images") == ids.index("characters") + 1
    char_step = next(s for s in steps if s["id"] == "characters")
    assert char_step["label"] == "Character bible"
    assert char_step["status"] == "pending"
    assert char_step["detail"] == "—"


@patch("director_api.services.pipeline_status.compute_phase5_readiness")
@patch("director_api.services.pipeline_status.scenes_spoken_narration_coverage")
@patch("director_api.services.pipeline_status.scene_image_video_counts")
def test_compute_pipeline_status_characters_done_when_rows_exist(
    mock_scene_counts: MagicMock,
    mock_narr_cov: MagicMock,
    mock_p5: MagicMock,
) -> None:
    pid = uuid4()
    tenant = "t1"
    proj = MagicMock()
    proj.tenant_id = tenant
    proj.id = pid
    proj.workflow_phase = "scenes_planned"
    proj.director_output_json = {"ok": True}

    db = MagicMock()
    db.get.return_value = proj
    mock_scene_counts.return_value = (2, 1, 0, 1)
    mock_narr_cov.return_value = (0, 0)
    mock_p5.return_value = {"ready": False, "issues": []}
    db.scalar.side_effect = [1, 1, 3, 0]
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    db.scalars.return_value = scalars_result

    out = compute_pipeline_status(db, project_id=pid, tenant_id=tenant, storage_root=None)
    char_step = next(s for s in out["steps"] if s["id"] == "characters")
    assert char_step["status"] == "done"
    assert char_step["detail"] == "3 character(s)"
