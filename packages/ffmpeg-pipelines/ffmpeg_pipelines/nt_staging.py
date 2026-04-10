"""Windows-only: stage media under short %TEMP% paths for FFmpeg/ffprobe.

FFmpeg and ffprobe do not reliably open ``\\\\?\\``-prefixed argv paths; ``paths.ffmpeg_argv_path`` therefore
never emits that prefix. For paths longer than ``paths._WIN_FFMPEG_ARGV_PATH_MAX``, callers must stage (copy or
hardlink) under ``%TEMP%`` before invoking ffmpeg/ffprobe. Python stdlib I/O on long paths still uses
``paths.subprocess_fs_path`` where extended prefixes are required.

Environment (optional):

- ``DIRECTOR_NT_FFMPEG_STAGE``: ``auto`` (default), ``always``, or ``never`` / ``0``.
- ``DIRECTOR_NT_FFMPEG_STAGE_LEN``: min resolved path length (default 120) that triggers staging in ``auto`` mode.

In ``auto`` mode, staging also runs when any path exceeds the ffmpeg plaintext limit, when argv pressure is high
(see ``concat_should_use_short_temp``), or when per-path length exceeds ``DIRECTOR_NT_FFMPEG_STAGE_LEN``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.paths import (
    mkdir_parent,
    path_is_readable_file,
    path_requires_nt_ffmpeg_staging,
    subprocess_fs_path,
)


def _stage_mode() -> str:
    return os.environ.get("DIRECTOR_NT_FFMPEG_STAGE", "auto").strip().lower()


def _stage_len_threshold() -> int:
    try:
        return max(40, int(os.environ.get("DIRECTOR_NT_FFMPEG_STAGE_LEN", "120")))
    except ValueError:
        return 120


def inputs_need_staging(paths: list[Path]) -> bool:
    """True when any resolved input path is long enough to risk WinError 206 without staging."""
    if os.name != "nt":
        return False
    lim = _stage_len_threshold()
    return any(len(str(p.resolve(strict=False))) >= lim for p in paths)


def concat_should_use_short_temp(paths: list[Path], output: Path) -> bool:
    """
    When True, concat should read inputs and write the .part file only under a short tempfile directory,
    then copy the result to ``output``.
    """
    if os.name != "nt":
        return False
    mode = _stage_mode()
    if mode in ("0", "false", "never", "no"):
        return False
    if mode in ("1", "true", "always", "yes"):
        return True

    if any(path_requires_nt_ffmpeg_staging(p) for p in paths):
        return True
    if path_requires_nt_ffmpeg_staging(output):
        return True

    lim = _stage_len_threshold()
    if inputs_need_staging(paths):
        return True
    out_s = str(output.resolve(strict=False))
    if len(out_s) >= lim:
        return True
    total = sum(len(str(p.resolve(strict=False))) for p in paths) + len(out_s)
    if total >= 4800:
        return True
    # Many -i arguments (slideshow, concat batch) blow argv / CreateProcess limits even when each path is "short".
    if len(paths) >= 6:
        return True
    path_chars = sum(len(str(p.resolve(strict=False))) for p in paths)
    if path_chars + len(out_s) >= 2800:
        return True
    return False


def make_short_concat_staging_dir() -> Path:
    """Fresh directory under the system temp folder (typically a short path)."""
    return Path(
        tempfile.mkdtemp(
            prefix=f"ffcc_{uuid.uuid4().hex[:10]}_",
            dir=tempfile.gettempdir(),
        )
    )


def stage_inputs_as_hardlink_or_copy(paths: list[Path], staging: Path) -> list[Path]:
    """
    Place each input in ``staging`` as ``i000.ext`` … so FFmpeg only sees short paths.
    Tries ``os.link`` first (same volume), then copies.
    """
    staging.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for i, p in enumerate(paths):
        if not path_is_readable_file(p):
            raise FFmpegCompileError(f"input not found: {p}")
        suf = (p.suffix or ".mp4").lower()
        if len(suf) > 16:
            suf = suf[:16]
        dst = staging / f"i{i:03d}{suf}"
        src = subprocess_fs_path(p)
        try:
            os.link(src, str(dst))
        except OSError:
            shutil.copy2(src, str(dst))
        staged.append(dst)
    return staged


def copy_short_to_destination(src: Path, dst: Path) -> None:
    """Copy encoded output from a short temp path onto the final (possibly long) path."""
    mkdir_parent(dst)
    shutil.copy2(str(src), subprocess_fs_path(dst))


def audio_should_use_short_temp(paths: list[Path]) -> bool:
    """
    True when any of ``paths`` must be re-hosted under a short ``%TEMP%`` path before FFmpeg/ffprobe run.

    FFmpeg does not accept ``\\\\?\\`` argv prefixes (see ``paths.ffmpeg_argv_path``). Plain paths longer than
    ``_WIN_FFMPEG_ARGV_PATH_MAX`` are unsafe without staging, regardless of ``DIRECTOR_NT_FFMPEG_STAGE_LEN``.
    """
    if os.name != "nt":
        return False
    mode = _stage_mode()
    if mode in ("0", "false", "never", "no"):
        return False
    if mode in ("1", "true", "always", "yes"):
        return True
    if any(path_requires_nt_ffmpeg_staging(p) for p in paths):
        return True
    return inputs_need_staging(paths)
