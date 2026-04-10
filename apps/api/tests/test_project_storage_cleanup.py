"""Tests for project delete filesystem cleanup."""

from pathlib import Path
from uuid import uuid4

from director_api.storage.project_storage_cleanup import remove_generated_project_files


def test_remove_generated_project_files_keeps_exports(tmp_path: Path) -> None:
    pid = uuid4()
    assets = tmp_path / "assets" / str(pid)
    narr = tmp_path / "narrations" / str(pid)
    exports = tmp_path / "exports" / str(pid)
    (assets / "x").mkdir(parents=True)
    (narr / "y").mkdir(parents=True)
    (exports / "z").mkdir(parents=True)
    (exports / "final_cut.mp4").write_bytes(b"x")

    remove_generated_project_files(tmp_path, pid)

    assert not assets.exists()
    assert not narr.exists()
    assert (exports / "final_cut.mp4").is_file()
