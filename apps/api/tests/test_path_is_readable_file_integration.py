"""Guardrail: API uses path_is_readable_file for storage paths (Windows MAX_PATH / WinError 206)."""

from pathlib import Path

from ffmpeg_pipelines.paths import path_is_readable_file


def test_path_is_readable_file_true_for_plain_file(tmp_path: Path) -> None:
    f = tmp_path / "probe.bin"
    f.write_bytes(b"x")
    assert path_is_readable_file(f)


def test_path_is_readable_file_false_for_missing(tmp_path: Path) -> None:
    assert not path_is_readable_file(tmp_path / "nope.bin")
