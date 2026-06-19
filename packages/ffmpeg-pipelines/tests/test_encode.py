"""Unit tests for centralized FFmpeg encode settings."""

from __future__ import annotations

from ffmpeg_pipelines.encode import (
    VideoEncodeConfig,
    append_video_encode_args,
    default_libx264_config,
    ffmpeg_has_encoder,
    nvenc_preset_map,
    resolve_video_encoder,
    video_encode_config_from_settings,
)


def test_nvenc_preset_map() -> None:
    assert nvenc_preset_map("veryfast") == "p4"
    assert nvenc_preset_map("medium") == "p6"
    assert nvenc_preset_map("unknown") == "p4"


def test_append_video_encode_args_libx264() -> None:
    cfg = default_libx264_config(crf=21, preset="fast")
    cmd: list[str] = []
    append_video_encode_args(cmd, cfg)
    assert cmd == ["-c:v", "libx264", "-preset", "fast", "-crf", "21"]


def test_append_video_encode_args_nvenc() -> None:
    cfg = VideoEncodeConfig(codec="h264_nvenc", preset="p4", quality=23)
    cmd: list[str] = []
    append_video_encode_args(cmd, cfg)
    assert "-c:v" in cmd and "h264_nvenc" in cmd
    assert "-cq" in cmd and "23" in cmd
    assert "-crf" not in cmd


def test_resolve_video_encoder_forces_libx264() -> None:
    assert resolve_video_encoder("ffmpeg", "libx264") == "libx264"


def test_video_encode_config_from_settings_libx264_preference(monkeypatch) -> None:
    monkeypatch.setattr(
        "ffmpeg_pipelines.encode.ffmpeg_has_encoder",
        lambda _bin, name: name == "h264_nvenc",
    )
    cfg = video_encode_config_from_settings(
        ffmpeg_bin="ffmpeg",
        encoder_preference="libx264",
        crf=22,
        preset="veryfast",
    )
    assert cfg.codec == "libx264"
    assert cfg.preset == "veryfast"
    assert cfg.quality == 22


def test_video_encode_config_auto_picks_nvenc_when_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "ffmpeg_pipelines.encode.ffmpeg_has_encoder",
        lambda _bin, name: name == "h264_nvenc",
    )
    cfg = video_encode_config_from_settings(ffmpeg_bin="ffmpeg", encoder_preference="auto")
    assert cfg.codec == "h264_nvenc"
    assert cfg.preset == "p4"


def test_ffmpeg_has_encoder_missing_binary() -> None:
    assert ffmpeg_has_encoder("definitely-not-a-real-ffmpeg-binary-xyz", "libx264") is False
