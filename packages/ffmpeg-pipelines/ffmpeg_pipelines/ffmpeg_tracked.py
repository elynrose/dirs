"""Optional registry for long-running FFmpeg subprocesses (agent-run cancel / reaper)."""

from __future__ import annotations

import subprocess
from typing import Protocol


class ExportFfmpegRegistry(Protocol):
    def attach(self, proc: subprocess.Popen) -> None: ...
    def detach(self, proc: subprocess.Popen) -> None: ...


def run_ffmpeg_tracked(
    args: list[str],
    *,
    timeout_sec: float,
    export_ffmpeg_registry: object | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg; when a registry is provided, track the Popen for cooperative cancel."""
    if export_ffmpeg_registry is None:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    export_ffmpeg_registry.attach(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    finally:
        export_ffmpeg_registry.detach(proc)
