"""Optional ``pip install`` for Kokoro / Chatterbox when imports fail (worker venv)."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import subprocess
import sys
from pathlib import Path

from director_api.config import Settings

log = logging.getLogger(__name__)

# Monorepo root (director/) — …/director_api/providers/this_file → five levels up
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _run_pip_install(args: list[str], *, timeout_sec: float) -> None:
    cmd = [sys.executable, "-m", "pip", "install", "--no-input", *args]
    log.warning("tts_auto_pip_install: running %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-4000:]
        raise RuntimeError(
            f"pip install failed (exit {proc.returncode}): {tail.strip() or 'no output'}"
        )


def _chatterbox_package_dir(settings: Settings) -> Path:
    raw = (getattr(settings, "chatterbox_editable_path", None) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_REPO_ROOT / "packages" / "chatterbox-tts").resolve()


def ensure_kokoro_importable(settings: Settings) -> None:
    """Import ``kokoro`` and ``soundfile``; optionally ``pip install`` when enabled."""
    if importlib.util.find_spec("kokoro") is not None and importlib.util.find_spec("soundfile") is not None:
        return
    if not bool(getattr(settings, "tts_auto_pip_install", False)):
        raise ValueError(
            "Kokoro is not installed. Install: pip install -e \".[kokoro]\" from apps/api "
            "(or set TTS_AUTO_PIP_INSTALL=1)."
        )
    timeout = float(getattr(settings, "tts_auto_pip_timeout_sec", 1200.0) or 1200.0)
    specs = [
        (getattr(settings, "kokoro_pip_kokoro_spec", None) or "kokoro>=0.9.4").strip() or "kokoro>=0.9.4",
        (getattr(settings, "kokoro_pip_soundfile_spec", None) or "soundfile>=0.12.1").strip()
        or "soundfile>=0.12.1",
    ]
    _run_pip_install(specs, timeout_sec=timeout)
    importlib.invalidate_caches()
    if importlib.util.find_spec("kokoro") is None or importlib.util.find_spec("soundfile") is None:
        raise RuntimeError("Kokoro pip install finished but kokoro/soundfile still not importable.")


def ensure_chatterbox_importable(settings: Settings) -> None:
    """Ensure vendored ``chatterbox`` (+ deps) is importable; optionally ``pip install -e``."""
    pkg = _chatterbox_package_dir(settings)
    if not (pkg / "pyproject.toml").is_file():
        raise ValueError(
            f"Chatterbox package missing at {pkg}. Clone the repo with packages/chatterbox-tts or set CHATTERBOX_EDITABLE_PATH."
        )

    def chatterbox_ok() -> bool:
        try:
            importlib.import_module("chatterbox.tts_turbo")
            return True
        except ImportError:
            return False

    if chatterbox_ok():
        return
    if not bool(getattr(settings, "tts_auto_pip_install", False)):
        raise ValueError(
            "chatterbox is not installed. Install: pip install -e \".[chatterbox]\" from apps/api "
            "(or set TTS_AUTO_PIP_INSTALL=1)."
        )
    timeout = float(getattr(settings, "tts_auto_pip_timeout_sec", 1200.0) or 1200.0)
    _run_pip_install(["-e", str(pkg)], timeout_sec=timeout)
    importlib.invalidate_caches()
    if not chatterbox_ok():
        raise RuntimeError("Chatterbox pip install finished but chatterbox still not importable.")
