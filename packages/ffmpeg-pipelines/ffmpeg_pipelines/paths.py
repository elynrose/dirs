"""Resolve Director storage URLs to local paths."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

# Below the legacy 260-char MAX_PATH, normal Win32 APIs work. Above this, use ``\\\\?\\`` for Python stdlib
# (``Path``, ``shutil``, ``os.replace``, etc.) so long paths remain addressable.
_WIN_LONG_THRESHOLD = 200

# FFmpeg and ffprobe mishandle ``\\\\?\\`` in argv (treat as literal / fail to open). Pass only plain resolved
# paths to them, and stage/copy to ``%TEMP%`` when a path would exceed a safe legacy length without ``\\\\?\\``.
_WIN_FFMPEG_ARGV_PATH_MAX = 240


def ffmpeg_argv_path(path: Path) -> str:
    """
    Path string for **ffmpeg** / **ffprobe** subprocess argv on Windows.

    Never emits ``\\\\?\\`` — those prefixes break FFmpeg's path parsing. Callers must stage long inputs/outputs
    under a short directory (see ``nt_staging``) when ``path_requires_nt_ffmpeg_staging`` is true.
    """
    s = str(path.resolve(strict=False))
    if os.name != "nt":
        return s
    # Path.resolve() can return \\?\-prefixed strings on Windows for long paths.
    # FFmpeg cannot parse that prefix — strip it unconditionally.
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]  # \\?\UNC\server\share → \\server\share
    if s.startswith("\\\\?\\"):
        return s[4:]  # \\?\C:\... → C:\...
    return s


def path_requires_nt_ffmpeg_staging(path: Path) -> bool:
    """True on Windows when ``path`` is too long to pass safely as a plain ffmpeg/ffprobe argv string."""
    if os.name != "nt":
        return False
    return len(str(path.resolve(strict=False))) > _WIN_FFMPEG_ARGV_PATH_MAX


def subprocess_fs_path(path: Path) -> str:
    """
    Path string for **Python** stdlib operations on Windows (``shutil``, ``os.link``, ``Path.mkdir``, etc.).

    Uses the ``\\\\?\\`` extended-length form when the resolved path is long. Do **not** use this for ffmpeg or
    ffprobe command lines — use ``ffmpeg_argv_path`` after staging, or plain short paths.
    """
    if os.name != "nt":
        return str(path.resolve(strict=False))
    resolved = path.resolve(strict=False)
    s = str(resolved)
    if s.startswith("\\\\?\\"):
        return s
    if len(s) < _WIN_LONG_THRESHOLD:
        return s
    # UNC: \\server\share\... → \\?\UNC\server\share\...
    # (The \\?\ check above already returned, so s cannot start with \\?\ here.)
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def path_is_readable_file(path: Path) -> bool:
    """File existence check that still works when the path exceeds legacy MAX_PATH on Windows."""
    if os.name != "nt":
        return path.resolve(strict=False).is_file()
    s = str(path.resolve(strict=False))
    if len(s) < _WIN_LONG_THRESHOLD:
        return Path(s).is_file()
    return Path(subprocess_fs_path(path)).is_file()


def mkdir_parent(path: Path) -> None:
    """Create the parent directory of ``path`` (handles long Windows paths)."""
    parent = path.resolve(strict=False).parent
    if os.name != "nt":
        parent.mkdir(parents=True, exist_ok=True)
        return
    if len(str(parent)) < _WIN_LONG_THRESHOLD:
        parent.mkdir(parents=True, exist_ok=True)
        return
    Path(subprocess_fs_path(parent)).mkdir(parents=True, exist_ok=True)


def replace_file_atomically(src: Path, dst: Path) -> None:
    """Rename ``src`` over ``dst`` (Windows-safe for long paths)."""
    if os.name != "nt":
        src.replace(dst)
        return
    os.replace(subprocess_fs_path(src), subprocess_fs_path(dst))


def path_stat(path: Path) -> os.stat_result:
    """``stat()`` that works for long paths passed to FFmpeg outputs on Windows."""
    if os.name != "nt":
        return path.stat()
    return Path(subprocess_fs_path(path)).stat()


def unlink_optional(path: Path) -> None:
    """Delete a file if it exists (Windows: retry with extended path after WinError 206)."""
    if os.name != "nt":
        path.unlink(missing_ok=True)
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        try:
            Path(subprocess_fs_path(path)).unlink(missing_ok=True)
        except OSError:
            pass


def path_from_storage_url(url: str | None, *, storage_root: Path) -> Path | None:
    """Map `file://…` or a storage-relative key to a concrete path."""
    if not url or not str(url).strip():
        return None
    raw = str(url).strip().replace("\\", "/")
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        path_part = parsed.path or ""
        nl = (parsed.netloc or "").strip()

        # Legacy bug: file://D:/dir/file (backslashes normalized) → netloc "D:", path "/dir/file"
        if nl and nl != "localhost" and len(nl) == 2 and nl[1] == ":" and path_part.startswith("/"):
            path = Path(unquote(f"{nl}{path_part}"))
        # RFC 8089 Windows: file:///D:/dir/file → path "/D:/dir/file", empty netloc
        elif (
            path_part.startswith("/")
            and len(path_part) >= 3
            and path_part[2] == ":"
            and path_part[1].isalpha()
        ):
            path = Path(unquote(path_part[1:]))
        elif nl and nl != "localhost":
            # UNC / non-local host: file://server/share/...
            combined = unquote(f"//{nl}{path_part}")
            path = Path(combined)
        else:
            path = Path(unquote(path_part or "/"))
        if not path.is_absolute() and storage_root:
            path = (storage_root / path).resolve()
        return path.resolve()
    key = raw.lstrip("/").replace("..", "")
    return (storage_root / key).resolve()
