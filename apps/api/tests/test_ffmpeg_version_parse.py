"""Startup FFmpeg version line parsing."""

from director_api.main import _ffmpeg_reported_major_version


def test_ffmpeg_major_from_standard_line() -> None:
    assert _ffmpeg_reported_major_version("ffmpeg version 6.1.1 Copyright") == 6


def test_ffmpeg_major_from_generic_version() -> None:
    assert _ffmpeg_reported_major_version("ffprobe version 5.0.1") == 5


def test_ffmpeg_major_unknown_returns_none() -> None:
    assert _ffmpeg_reported_major_version("ffmpeg version N-123456-gabcdef") is None
