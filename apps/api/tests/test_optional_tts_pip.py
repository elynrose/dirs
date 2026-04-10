"""optional_tts_pip layout (no real pip install in CI)."""

from pathlib import Path

from director_api.config import Settings
from director_api.providers.optional_tts_pip import _chatterbox_package_dir


def test_chatterbox_package_dir_default_points_at_vendor():
    s = Settings()
    p = _chatterbox_package_dir(s)
    assert p.name == "chatterbox-tts"
    assert (p / "pyproject.toml").is_file(), f"missing vendor tree at {p}"


def test_chatterbox_package_dir_override(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    s = Settings(chatterbox_editable_path=str(tmp_path))
    assert _chatterbox_package_dir(s) == tmp_path.resolve()
