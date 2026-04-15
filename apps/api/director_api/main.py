from __future__ import annotations

import shutil
import subprocess
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from director_api.api.routers import (
    admin_api,
    agent_runs,
    auth,
    billing,
    chat_studio,
    events,
    fal_catalog,
    health,
    integrations_telegram,
    integrations_youtube,
    jobs,
    narration_styles,
    project_characters,
    project_pipeline,
    projects,
    prompts,
    settings,
    workflow_phase2,
    workflow_phase3,
    workflow_phase4,
    workflow_phase5,
)
from director_api.config import get_settings
from director_api.logging_config import configure_logging
from director_api.middleware.rate_limit import TenantRateLimitMiddleware
from director_api.middleware.request_context import RequestContextMiddleware

log = structlog.get_logger(__name__)


def _cors_allow_origins() -> list[str]:
    base = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]
    # Vite picks the next free port when 5173/5174/… are taken (parallel dev servers).
    for _p in range(5174, 5181):
        base.append(f"http://127.0.0.1:{_p}")
        base.append(f"http://localhost:{_p}")
    # Electron static UI server tries 4174–4178 (see apps/electron/main.js get-port).
    for _p in range(4174, 4179):
        base.append(f"http://127.0.0.1:{_p}")
        base.append(f"http://localhost:{_p}")
    extra_raw = (get_settings().cors_extra_origins or "").strip()
    if extra_raw:
        for part in extra_raw.split(","):
            o = part.strip()
            if o:
                base.append(o)
    return list(dict.fromkeys(base))


def _check_ffmpeg_binaries() -> None:
    """Warn at startup if ffmpeg/ffprobe are missing from PATH.

    We log a warning rather than raising so the API can still serve
    non-compile routes when FFmpeg is absent.  The compile endpoints will
    fail with a clear error message when they are actually invoked.
    """
    cfg = get_settings()
    for label, binary in (("ffmpeg", cfg.ffmpeg_bin), ("ffprobe", cfg.ffprobe_bin)):
        resolved = shutil.which(binary)
        if resolved is None:
            log.warning(
                "ffmpeg_binary_not_found",
                label=label,
                binary=binary,
                hint="Install ffmpeg or set FFMPEG_BIN / FFPROBE_BIN in .env",
            )
        else:
            try:
                result = subprocess.run(
                    [resolved, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                first_line = (result.stdout or result.stderr or "").splitlines()[0]
                log.info("ffmpeg_binary_ok", label=label, binary=resolved, version=first_line)
            except Exception as exc:
                log.warning("ffmpeg_binary_error", label=label, binary=resolved, error=str(exc))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    _check_ffmpeg_binaries()
    s = get_settings()
    secret = (s.director_jwt_secret or "").strip()
    if secret:
        from director_api.auth.jwtutil import jwt_secret_is_weak

        if jwt_secret_is_weak(s.director_jwt_secret):
            log.warning(
                "director_jwt_secret_weak",
                hint="DIRECTOR_JWT_SECRET is used for OAuth state signing (e.g. YouTube). Use a long random value (32+ bytes). Production should set DIRECTOR_JWT_REJECT_WEAK_SECRET=1.",
            )
            if s.director_jwt_reject_weak_secret:
                raise RuntimeError(
                    "DIRECTOR_JWT_SECRET is too weak while DIRECTOR_JWT_REJECT_WEAK_SECRET is enabled"
                )
    yield


_cfg = get_settings()
app = FastAPI(
    title="Directely API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _cfg.director_expose_openapi else None,
    redoc_url="/redoc" if _cfg.director_expose_openapi else None,
    openapi_url="/openapi.json" if _cfg.director_expose_openapi else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length"],
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(TenantRateLimitMiddleware)
app.include_router(health.router, prefix="/v1")
app.include_router(admin_api.router, prefix="/v1")
app.include_router(auth.router, prefix="/v1")
app.include_router(billing.router, prefix="/v1")
app.include_router(chat_studio.router, prefix="/v1")
app.include_router(events.router, prefix="/v1")
app.include_router(projects.router, prefix="/v1")
app.include_router(project_characters.router, prefix="/v1")
app.include_router(project_pipeline.router, prefix="/v1")
app.include_router(agent_runs.router, prefix="/v1")
app.include_router(workflow_phase2.router, prefix="/v1")
app.include_router(workflow_phase3.router, prefix="/v1")
app.include_router(workflow_phase4.router, prefix="/v1")
app.include_router(workflow_phase5.router, prefix="/v1")
app.include_router(fal_catalog.router, prefix="/v1")
app.include_router(jobs.router, prefix="/v1")
app.include_router(settings.router, prefix="/v1")
app.include_router(integrations_telegram.router, prefix="/v1")
app.include_router(integrations_youtube.router, prefix="/v1")
app.include_router(prompts.router, prefix="/v1")
app.include_router(narration_styles.router, prefix="/v1")


# ---------------------------------------------------------------------------
# Centralised exception → {"error": {...}} envelope
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def _validation_handler(_request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "request validation failed",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(_request, exc: HTTPException):
    """Normalise FastAPI HTTPException to the standard error envelope.

    Routers that pass ``detail={"code": ..., "message": ...}`` get that dict
    forwarded directly.  Plain string details are wrapped automatically.
    """
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        error = detail
    else:
        error = {"code": "HTTP_ERROR", "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(Exception)
async def _unhandled_exception_handler(_request, exc: Exception):
    """Catch-all so unhandled exceptions never leak stack traces to clients."""
    log.exception("unhandled_exception", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "an unexpected error occurred"}},
    )
