"""Scene coverage slot counts for pipeline status."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from director_api.services.scene_coverage import project_scene_coverage_counts


@patch("director_api.services.scene_coverage.effective_scene_visual_budget_sec")
def test_project_scene_coverage_counts_met(mock_budget: MagicMock) -> None:
    pid = uuid4()
    sc1 = MagicMock()
    sc1.id = uuid4()
    sc2 = MagicMock()
    sc2.id = uuid4()
    db = MagicMock()
    db.scalars.return_value.all.return_value = [sc1, sc2]
    mock_budget.side_effect = [12.0, 5.0]

    with patch("director_api.services.scene_coverage._scene_succeeded_visual_count", side_effect=[3, 1]):
        tot, met, have, need = project_scene_coverage_counts(
            db,
            pid,
            storage_root="/tmp",
            clip_sec=5.0,
            tail_padding_sec=0.5,
        )

    assert tot == 2
    assert met == 2
    assert need == 4  # ceil(12/5) + ceil(5/5)
    assert have == 4
