import subprocess
import sys
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Request
from redis import Redis
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from director_api.api.deps import meta_dep, settings_dep
from director_api.api.security_ops import assert_ops_route_allowed
from director_api.config import Settings, get_settings
from director_api.db.session import get_db
from director_api.db.models import Job
from director_api.services.celery_liveness import build_celery_status_data

router = APIRouter(tags=["health"])
log = structlog.get_logger(__name__)


def _director_repo_root() -> Path:
    """Resolve the git repo root (contains ``scripts/`` and ``apps/api``)."""
    here = Path(__file__).resolve()
    for d in here.parents:
        if (d / "scripts" / "restart-celery-worker.ps1").is_file():
            return d
    for d in here.parents:
        if (d / "apps" / "api" / "director_api").is_dir():
            return d
    return here.parents[5]


def _win_spawn_worker_flags() -> int:
    if sys.platform != "win32":
        return 0
    detached = 0x00000008  # DETACHED_PROCESS
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return detached | no_window


@router.get("/health")
def health(meta: dict = Depends(meta_dep)) -> dict:
    return {"data": {"status": "ok"}, "meta": meta}


@router.get("/ready")
def ready(
    db: Session = Depends(get_db),
    meta: dict = Depends(meta_dep),
) -> dict:
    settings = get_settings()
    errors: list[str] = []
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        errors.append(f"database:{e!s}")
    try:
        r = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        r.close()
    except Exception as e:  # noqa: BLE001
        errors.append(f"redis:{e!s}")
    if errors:
        structlog.get_logger(__name__).warning("ready_failed", errors=errors)
        return {"data": {"status": "not_ready", "errors": errors}, "meta": meta}
    return {"data": {"status": "ready"}, "meta": meta}


@router.get("/metrics")
def metrics(
    request: Request,
    db: Session = Depends(get_db),
    meta: dict = Depends(meta_dep),
) -> dict:
    assert_ops_route_allowed(request)
    settings = get_settings()
    rows = db.execute(
        select(Job.status, func.count(Job.id)).where(Job.tenant_id == settings.default_tenant_id).group_by(Job.status)
    ).all()
    by_status = {str(r[0]): int(r[1]) for r in rows}
    return {
        "data": {
            "tenant_id": settings.default_tenant_id,
            "jobs_by_status": by_status,
            "job_caps": {
                "enforced": settings.job_caps_enforced,
                "media": settings.job_cap_media,
                "compile": settings.job_cap_compile,
                "text": settings.job_cap_text,
                "media_global": settings.job_cap_media_global,
            },
            "scene_clip_duration_sec": settings.scene_clip_duration_sec,
            "scene_vo_tail_padding_sec": settings.scene_vo_tail_padding_sec,
        },
        "meta": meta,
    }


@router.get("/celery/status")
def celery_status(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Ping Celery workers; if ping fails during a long solo-pool task, infer *online* from DB."""
    return {
        "data": build_celery_status_data(db, settings.default_tenant_id),
        "meta": meta,
    }


@router.post("/celery/restart")
def celery_restart(request: Request, meta: dict = Depends(meta_dep)) -> dict:
    assert_ops_route_allowed(request)
    """Kill existing Celery workers and start a fresh one.

    Uses the repo's ``scripts/restart-celery-worker.ps1`` on Windows,
    falling back to a direct kill-and-spawn sequence.
    """
    repo_root = _director_repo_root()
    script = repo_root / "scripts" / "restart-celery-worker.ps1"

    if sys.platform == "win32" and script.is_file():
        try:
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ],
                cwd=str(repo_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_win_spawn_worker_flags(),
            )
            log.info("celery_restart_triggered", method="ps1_script", repo_root=str(repo_root))
            return {"data": {"status": "restarting", "method": "ps1_script"}, "meta": meta}
        except Exception as exc:  # noqa: BLE001
            log.warning("celery_restart_ps1_failed", error=str(exc)[:300])

    api_dir = repo_root / "apps" / "api"
    venv_python = api_dir / ".venv-win" / "Scripts" / "python.exe"
    if not venv_python.is_file():
        venv_python = api_dir / ".venv" / "Scripts" / "python.exe"
    celery_exe = venv_python.parent / "celery.exe"

    try:
        if sys.platform == "win32":
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process -EA SilentlyContinue | "
                    "Where-Object { $_.CommandLine -and "
                    "$_.CommandLine -match 'director_api\\.tasks\\.celery_app' -and "
                    "$_.CommandLine -match '\\sworker\\s' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }",
                ],
                timeout=30,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            subprocess.run(["pkill", "-f", "director_api.tasks.celery_app.*worker"], timeout=5, check=False)

        exe = str(celery_exe) if celery_exe.is_file() else str(venv_python)
        args = ([exe] if celery_exe.is_file() else [exe, "-m", "celery"])
        args += ["-A", "director_api.tasks.celery_app", "worker", "-l", "info"]
        if sys.platform == "win32":
            args += ["--pool=solo"]

        subprocess.Popen(
            args,
            cwd=str(api_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_win_spawn_worker_flags(),
        )
        log.info("celery_restart_triggered", method="direct_spawn", api_dir=str(api_dir))
        return {"data": {"status": "restarting", "method": "direct_spawn"}, "meta": meta}
    except Exception as exc:  # noqa: BLE001
        log.error("celery_restart_failed", error=str(exc)[:300])
        return {"data": {"status": "error", "error": str(exc)[:300]}, "meta": meta}
