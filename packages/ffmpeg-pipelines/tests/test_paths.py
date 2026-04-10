import sys

import pytest
from pathlib import Path

from ffmpeg_pipelines.paths import ffmpeg_argv_path, path_from_storage_url, subprocess_fs_path


def test_file_url_absolute(tmp_path: Path):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    url = f.as_uri()
    p = path_from_storage_url(url, storage_root=tmp_path)
    assert p == f.resolve()


def test_relative_key_under_storage_root(tmp_path: Path):
    sub = tmp_path / "assets" / "p" / "x.png"
    sub.parent.mkdir(parents=True)
    sub.write_bytes(b"y")
    p = path_from_storage_url("assets/p/x.png", storage_root=tmp_path)
    assert p == sub.resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file:///D:/... path shape")
def test_file_uri_windows_as_uri_roundtrip(tmp_path: Path):
    """Matches Path.as_uri() from FilesystemStorage.put_bytes after the storage URL fix."""
    f = tmp_path / "win-round.png"
    f.write_bytes(b"x")
    uri = f.resolve().as_uri()
    assert uri.startswith("file:///")
    p = path_from_storage_url(uri, storage_root=tmp_path)
    assert p.resolve() == f.resolve()
    assert p.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="subprocess_fs_path extended prefix is Windows-specific")
def test_subprocess_fs_path_adds_extended_prefix_for_long_path(tmp_path: Path):
    """Long full paths need \\\\?\\ for Python stdlib I/O (WinError 206 without it)."""
    root = tmp_path.resolve(strict=False)
    # One long filename (≤255) so the full path crosses _WIN_LONG_THRESHOLD.
    pad = max(1, min(255, 250 - len(str(root))))
    f = root / ("n" * pad)
    assert len(str(f.resolve(strict=False))) >= 240
    assert subprocess_fs_path(f).startswith("\\\\?\\")


@pytest.mark.skipif(sys.platform != "win32", reason="ffmpeg_argv_path behavior is Windows-specific")
def test_ffmpeg_argv_path_never_uses_extended_prefix(tmp_path: Path):
    """FFmpeg mis-parses \\\\?\\ in argv; ffmpeg_argv_path stays plain (staging handles long paths)."""
    root = tmp_path.resolve(strict=False)
    pad = max(1, min(255, 250 - len(str(root))))
    f = root / ("m" * pad)
    assert len(str(f.resolve(strict=False))) >= 240
    s = ffmpeg_argv_path(f)
    assert not s.startswith("\\\\?\\")
    assert "\\\\?\\" not in s


def test_legacy_file_uri_backslash_normalized(tmp_path: Path):
    """Old URLs used file:// + Path str with backslashes; normalize to forward slashes."""
    f = tmp_path / "legacy.png"
    f.write_bytes(b"z")
    posix = f.resolve().as_posix()
    # Simulate broken urlparse split: netloc holds drive+path segment (Windows only meaningful)
    if sys.platform == "win32" and len(posix) >= 2 and posix[1] == ":":
        drive, rest = posix[0], posix[2:].lstrip("/")
        legacy = f"file://{drive}:/{rest}"
        p = path_from_storage_url(legacy.replace("\\", "/"), storage_root=tmp_path)
        assert p.resolve() == f.resolve()
