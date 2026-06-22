"""Pipeline status aggregation for Studio inspector (character bible row + ordering)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from director_api.services.pipeline_status import compute_pipeline_status


@patch("director_api.services.publish_hook.find_hook_scene")
@patch("director_api.services.publish_outro.find_outro_scene")
@patch("director_api.services.publish_pack.publish_pack_done")
@patch("director_api.services.pipeline_status.compute_phase5_readiness")
@patch("director_api.services.pipeline_status.scenes_spoken_narration_coverage")
@patch("director_api.services.pipeline_status.scene_image_video_counts")
def test_compute_pipeline_status_characters_row_after_scenes_pending_when_empty(
    mock_scene_counts: MagicMock,
    mock_narr_cov: MagicMock,
    mock_p5: MagicMock,
    mock_publish_done: MagicMock,
    mock_find_outro: MagicMock,
    mock_find_hook: MagicMock,
) -> None:
    pid = uuid4()
    tenant = "t1"
    proj = MagicMock()
    proj.tenant_id = tenant
    proj.id = pid
    proj.workflow_phase = "scenes_planned"
    proj.director_output_json = {"ok": True}
    proj.publish_pack_json = None
    proj.opening_hook_text = None
    proj.include_outro_scene = False
    proj.use_all_approved_scene_media = False

    db = MagicMock()
    db.get.return_value = proj
    mock_scene_counts.return_value = (2, 0, 0, 0)
    mock_narr_cov.return_value = (0, 0)
    mock_p5.return_value = {"ready": False, "issues": []}
    mock_publish_done.return_value = False
    mock_find_outro.return_value = None
    mock_find_hook.return_value = None
    db.scalar.side_effect = [1, 1, 0, 0]
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    db.scalars.return_value = scalars_result

    out = compute_pipeline_status(db, project_id=pid, tenant_id=tenant, storage_root=None)
    assert out["ok"] is True
    steps = out["steps"]
    ids = [s["id"] for s in steps]
    assert ids.index("thumbnail") == ids.index("chapters") + 1
    assert ids.index("opening_hook") == ids.index("thumbnail") + 1
    assert ids.index("scenes") == ids.index("opening_hook") + 1
    assert ids.index("outro") == ids.index("scenes") + 1
    assert ids.index("story_research_review") == ids.index("outro") + 1
    assert ids.index("characters") == ids.index("story_research_review") + 1
    assert ids.index("narration") == ids.index("characters") + 1
    assert ids.index("scene_coverage") == ids.index("narration") + 1
    assert ids.index("images") == ids.index("scene_coverage") + 1
    char_step = next(s for s in steps if s["id"] == "characters")
    assert char_step["label"] == "Character bible"
    assert char_step["status"] == "pending"
    assert char_step["detail"] == "—"


@patch("director_api.services.publish_hook.find_hook_scene")
@patch("director_api.services.publish_outro.find_outro_scene")
@patch("director_api.services.publish_pack.publish_pack_done")
@patch("director_api.services.pipeline_status.compute_phase5_readiness")
@patch("director_api.services.pipeline_status.scenes_spoken_narration_coverage")
@patch("director_api.services.pipeline_status.scene_image_video_counts")
def test_compute_pipeline_status_characters_done_when_rows_exist(
    mock_scene_counts: MagicMock,
    mock_narr_cov: MagicMock,
    mock_p5: MagicMock,
    mock_publish_done: MagicMock,
    mock_find_outro: MagicMock,
    mock_find_hook: MagicMock,
) -> None:
    pid = uuid4()
    tenant = "t1"
    proj = MagicMock()
    proj.tenant_id = tenant
    proj.id = pid
    proj.workflow_phase = "scenes_planned"
    proj.director_output_json = {"ok": True}
    proj.publish_pack_json = None
    proj.opening_hook_text = None
    proj.include_outro_scene = False
    proj.use_all_approved_scene_media = False

    db = MagicMock()
    db.get.return_value = proj
    mock_scene_counts.return_value = (2, 1, 0, 1)
    mock_narr_cov.return_value = (0, 0)
    mock_p5.return_value = {"ready": False, "issues": []}
    mock_publish_done.return_value = False
    mock_find_outro.return_value = None
    mock_find_hook.return_value = None
    db.scalar.side_effect = [1, 1, 3, 0]
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    db.scalars.return_value = scalars_result

    out = compute_pipeline_status(db, project_id=pid, tenant_id=tenant, storage_root=None)
    char_step = next(s for s in out["steps"] if s["id"] == "characters")
    assert char_step["status"] == "done"
    assert char_step["detail"] == "3 · characters"
    cov_step = next(s for s in out["steps"] if s["id"] == "scene_coverage")
    assert cov_step["status"] == "skipped"
    assert cov_step["detail"] == "Off in Settings"
