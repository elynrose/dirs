"""Centralized FFmpeg video encode settings (libx264 / h264_nvenc)."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Literal

EncoderPreference = Literal["auto", "libx264", "h264_nvenc"]
VideoCodec = Literal["libx264", "h264_nvenc"]

_NVENC_PRESET_MAP: dict[str, str] = {
    "ultrafast": "p1",
    "superfast": "p2",
    "veryfast": "p4",
    "faster": "p4",
    "fast": "p5",
    "medium": "p6",
    "slow": "p7",
    "slower": "p7",
    "veryslow": "p7",
}


@dataclass(frozen=True)
class VideoEncodeConfig:
    codec: VideoCodec
    preset: str
    quality: int
    pix_fmt: str = "yuv420p"

    @property
    def crf(self) -> int:
        return self.quality

    def as_compile_meta(self) -> dict[str, Any]:
        return {
            "codec": self.codec,
            "preset": self.preset,
            "crf": self.quality,
            "cq": self.quality if self.codec == "h264_nvenc" else None,
        }


def nvenc_preset_map(cpu_preset: str) -> str:
    key = (cpu_preset or "veryfast").strip().lower()
    return _NVENC_PRESET_MAP.get(key, "p4")


def default_libx264_config(*, crf: int = 23, preset: str = "veryfast", pix_fmt: str = "yuv420p") -> VideoEncodeConfig:
    return VideoEncodeConfig(codec="libx264", preset=preset, quality=crf, pix_fmt=pix_fmt)


def effective_encode_config(
    encode_config: VideoEncodeConfig | None,
    *,
    crf: int = 23,
    preset: str = "veryfast",
    pix_fmt: str = "yuv420p",
) -> VideoEncodeConfig:
    if encode_config is not None:
        return encode_config
    return default_libx264_config(crf=crf, preset=preset, pix_fmt=pix_fmt)


def ffmpeg_has_encoder(ffmpeg_bin: str, encoder_name: str) -> bool:
    if not shutil.which(ffmpeg_bin):
        return False
    proc = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    if proc.returncode != 0:
        return False
    return encoder_name in (proc.stdout or "")


def resolve_video_encoder(ffmpeg_bin: str, preference: EncoderPreference = "auto") -> VideoCodec:
    if preference == "libx264":
        return "libx264"
    if preference == "h264_nvenc":
        if ffmpeg_has_encoder(ffmpeg_bin, "h264_nvenc"):
            return "h264_nvenc"
        return "libx264"
    if ffmpeg_has_encoder(ffmpeg_bin, "h264_nvenc"):
        return "h264_nvenc"
    return "libx264"


def video_encode_config_from_settings(
    *,
    ffmpeg_bin: str,
    encoder_preference: EncoderPreference = "auto",
    crf: int = 23,
    preset: str = "veryfast",
    pix_fmt: str = "yuv420p",
) -> VideoEncodeConfig:
    codec = resolve_video_encoder(ffmpeg_bin, encoder_preference)
    eff_preset = nvenc_preset_map(preset) if codec == "h264_nvenc" else preset
    return VideoEncodeConfig(codec=codec, preset=eff_preset, quality=crf, pix_fmt=pix_fmt)


def append_video_encode_args(cmd: list[str], cfg: VideoEncodeConfig) -> None:
    if cfg.codec == "libx264":
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                cfg.preset,
                "-crf",
                str(cfg.quality),
            ]
        )
    elif cfg.codec == "h264_nvenc":
        cmd.extend(
            [
                "-c:v",
                "h264_nvenc",
                "-preset",
                cfg.preset,
                "-rc",
                "vbr",
                "-cq",
                str(cfg.quality),
                "-pix_fmt",
                cfg.pix_fmt,
            ]
        )
    else:
        raise ValueError(f"unsupported video codec: {cfg.codec}")
