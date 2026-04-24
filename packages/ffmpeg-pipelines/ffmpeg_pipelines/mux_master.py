"""Mux video with narration + optional music (web stereo master, -16 LUFS target)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.nt_staging import (
    audio_should_use_short_temp,
    copy_short_to_destination,
    make_short_concat_staging_dir,
    stage_inputs_as_hardlink_or_copy,
)
from ffmpeg_pipelines.paths import ffmpeg_argv_path, mkdir_parent, path_is_readable_file, path_stat
from ffmpeg_pipelines.probe import ffprobe_duration_seconds

# Stock / library beds are often mastered hot; scale the 0–1 slider before ``volume=`` so the
# same UI value sits further under narration after summing + loudnorm.
_MUSIC_SLIDER_TO_LINEAR_HEADROOM = 0.65


def mux_video_with_narration_and_music(
    video_path: Path,
    output_path: Path,
    *,
    narration_audio_path: Path | None = None,
    music_audio_path: Path | None = None,
    music_volume: float = 0.28,
    narration_volume: float = 1.0,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 900.0,
) -> dict[str, Any]:
    """
    Input video: **only the video stream is used** (``0:v``); any audio on the file is ignored.

    - If ``narration_audio_path`` is set and exists, it is trimmed/padded to video duration.
    - Otherwise a stereo silence track is generated for the video duration.
    - If ``music_audio_path`` is set and exists, it is looped, trimmed to video duration,
      attenuated, and **amix**'d under narration. The UI slider (0–1) is multiplied by
      ``_MUSIC_SLIDER_TO_LINEAR_HEADROOM`` before ``volume=`` so mastered beds sit under VO.
      ``amix`` uses ``normalize=0`` and ``dropout_transition=0`` so FFmpeg does not
      re-gain inputs when summing (``normalize=1`` would scale and break the intended blend).
    - After the mix, a fixed **0.5** linear gain prevents summing two hot sources from clipping
      before **loudnorm**; relative levels from the sliders are unchanged.
    - Output audio is **loudnorm** toward -16 LUFS integrated (single-pass ``linear=true``).
    """
    video_path = video_path.resolve()
    output_path = output_path.resolve()
    if not path_is_readable_file(video_path):
        raise FFmpegCompileError(f"video not found: {video_path}")

    # Resolve optional file paths; determine which inputs actually exist now so we know
    # what to stage before probing duration (ffprobe also needs a short path on Windows).
    narr_resolved = narration_audio_path.resolve() if narration_audio_path else None
    use_narr_file = bool(narr_resolved and path_is_readable_file(narr_resolved))
    music_resolved = music_audio_path.resolve() if music_audio_path else None
    use_music = bool(music_resolved and path_is_readable_file(music_resolved))

    # Collect all file-paths that FFmpeg / ffprobe will touch so we can stage them together.
    file_inputs: list[Path] = [video_path]
    if use_narr_file and narr_resolved:
        file_inputs.append(narr_resolved)
    if use_music and music_resolved:
        file_inputs.append(music_resolved)

    # On Windows, FFmpeg does not accept \\?\-prefixed paths in argv. When any path
    # (input or output) exceeds the safe threshold, stage everything under %TEMP%.
    st_root: Path | None = None
    try:
        if audio_should_use_short_temp([*file_inputs, output_path]):
            st_root = make_short_concat_staging_dir()
            staged = stage_inputs_as_hardlink_or_copy(file_inputs, st_root)
            video_use = staged[0]
            idx = 1
            narr_use: Path | None = None
            if use_narr_file:
                narr_use = staged[idx]
                idx += 1
            music_use: Path | None = staged[idx] if use_music else None
            out_write = st_root / "final_cut.mp4"
        else:
            video_use = video_path
            narr_use = narr_resolved if use_narr_file else None
            music_use = music_resolved if use_music else None
            out_write = output_path
            mkdir_parent(output_path)

        dur = ffprobe_duration_seconds(video_use, ffprobe_bin=ffprobe_bin, timeout_sec=timeout_sec)
        if dur <= 0:
            raise FFmpegCompileError("could not read positive video duration")

        dstr = f"{dur:.3f}"
        args: list[str] = [ffmpeg_bin, "-y", "-i", ffmpeg_argv_path(video_use)]

        if use_narr_file and narr_use is not None:
            args.extend(["-i", ffmpeg_argv_path(narr_use)])
        else:
            args.extend(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    dstr,
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                ]
            )

        narr_idx = 1
        music_idx: int | None = None
        if use_music and music_use is not None:
            music_idx = 2
            args.extend(["-stream_loop", "-1", "-i", ffmpeg_argv_path(music_use)])

        nv = max(0.0, min(float(narration_volume), 4.0))
        # Narration / silence → stereo 48 kHz, length matched to video
        if use_narr_file:
            narr_f = (
                f"[{narr_idx}:a]aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000,"
                f"atrim=0:{dstr},asetpts=PTS-STARTPTS,apad=whole_dur={dstr},volume={nv}[narr]"
            )
        else:
            narr_f = (
                f"[{narr_idx}:a]aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000,"
                f"volume={nv}[narr]"
            )

        mv_applied: float | None = None
        if use_music and music_idx is not None:
            mv_user = max(0.0, min(float(music_volume), 1.0))
            mv = mv_user * _MUSIC_SLIDER_TO_LINEAR_HEADROOM
            mv_applied = float(mv_user)
            music_f = (
                f"[{music_idx}:a]aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000,"
                f"atrim=0:{dstr},asetpts=PTS-STARTPTS,volume={mv}[mus];"
                f"[narr][mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amx];"
                f"[amx]volume=0.5[amh];"
                f"[amh]loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:print_format=summary[ao]"
            )
            filter_complex = narr_f + ";" + music_f
        else:
            filter_complex = narr_f + ";" + "[narr]loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:print_format=summary[ao]"

        args.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v:0",
                "-map",
                "[ao]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-t",
                dstr,
                ffmpeg_argv_path(out_write),
            ]
        )

        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-5000:]
            raise FFmpegCompileError(tail.strip() or "ffmpeg mux failed")
        if not path_is_readable_file(out_write) or path_stat(out_write).st_size < 64:
            raise FFmpegCompileError("mux produced empty output")

        if st_root is not None:
            copy_short_to_destination(out_write, output_path)

        return {
            "output_path": str(output_path),
            "bytes": path_stat(output_path).st_size,
            "video_duration_sec": dur,
            "mode": "mux_narration_music",
            "used_narration_file": use_narr_file,
            "used_music_file": use_music,
            "loudnorm_target_lufs": -16,
            "music_volume_applied": mv_applied,
            "music_linear_gain": float(mv) if use_music else None,
            "narration_volume_applied": float(nv),
        }
    finally:
        if st_root is not None:
            shutil.rmtree(st_root, ignore_errors=True)
