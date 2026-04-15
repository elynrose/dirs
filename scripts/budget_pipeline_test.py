#!/usr/bin/env python3
"""
Drive an Auto or Hands-off ``full_video`` agent run for smoke / CI.

**Default (cheap tests):** the brief pins placeholder images + local FFmpeg video and sets
``auto_generate_scene_videos: false`` so routine runs do not call paid image APIs or enqueue per-scene
video jobs. That is intentional: production Studio sends workspace providers from Settings and
typically enables scene videos — see ``--production-media`` below when you want that parity.

**Production parity (optional):** ``--production-media`` omits ``preferred_image_provider`` /
``preferred_video_provider`` on the brief (project inherits tenant workspace ``active_*_provider``
from the API like Studio) and sets ``auto_generate_scene_videos: true``. Use when you accept real
image/video API usage.

Narration: ``preferred_speech_provider`` is omitted in both modes so VO follows workspace
``active_speech_provider``. Set ``DIRECTOR_PLACEHOLDER_MEDIA=1`` on the worker to force placeholder
images for any project regardless of brief.

You still need a **text** LLM path (OpenAI, LM Studio, etc.) and whatever research uses (Tavily optional);
this script does not stub those.

Prerequisites
  - API + Celery worker running, DB/Redis up (same as normal Studio).
  - ffmpeg on PATH for the worker (already required for Directely).
  - Optional: set DIRECTOR_PLACEHOLDER_MEDIA=1 on the worker to force placeholder *images* for *any*
    project (overrides Studio image picks; narration still uses workspace TTS). Otherwise this script sets providers on the new project only.

Music
  - After the run is queued, uploads a local file as a music bed (default: ~/Downloads/Desert Covenant.mp3).
  - Adjust with --music-path. Use a path to any supported audio file you have.

Auth (when DIRECTOR_AUTH_ENABLED=true)
  - **Studio session:** POST /v1/auth/login (cookie jar), then POST /v1/agent-runs and poll GET /v1/agent-runs/{id}:
      python scripts/budget_pipeline_test.py --login-email you@example.com --login-password '…'
    Password can come from env instead: DIRECTOR_TEST_PASSWORD
  - **Platform admin (CI / no user account):** ``DIRECTOR_ADMIN_API_KEY`` or ``--admin-key`` enqueues
    ``POST /v1/admin/budget-pipeline-test`` and polls ``GET /v1/admin/agent-runs/{id}`` (music upload is skipped — it needs a session).

Logging
  - Default: INFO to stderr. Use ``-v`` / ``--verbose`` for DEBUG (every poll tick + request bodies).
  - Env ``BUDGET_PIPELINE_LOG_LEVEL=DEBUG`` also works.

Programmatic use
  - ``python -c "from scripts.budget_pipeline_test import run_budget_pipeline; import sys; sys.exit(run_budget_pipeline(['--mode','hands-off','--login-email','a@b.c']))"``
  - Or import ``run_budget_pipeline`` from another tool (pass argv without script name).

Examples
  python scripts/budget_pipeline_test.py --mode hands-off
  python scripts/budget_pipeline_test.py --mode hands-off --login-email you@example.com
  python scripts/budget_pipeline_test.py --mode auto --api-base http://127.0.0.1:8000
  python scripts/budget_pipeline_test.py --mode hands-off --production-media -v
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

try:
    import httpx
except ImportError as e:
    print("Install httpx in this interpreter (e.g. apps/api .venv: pip install httpx)", file=sys.stderr)
    raise SystemExit(2) from e

LOG = logging.getLogger("budget_pipeline_test")


def _configure_logging(level_name: str) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    lvl = getattr(logging, level_name.upper(), logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(h)
    root.setLevel(lvl)
    LOG.setLevel(lvl)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _truncate(s: str, n: int = 2000) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _log_response(method: str, url: str, resp: httpx.Response, *, ok_body: bool = False) -> None:
    extra = f" status={resp.status_code}"
    if resp.status_code >= 400:
        LOG.error("HTTP %s %s%s body=%s", method, url, extra, _truncate(resp.text, 4000))
    elif ok_body and LOG.isEnabledFor(logging.DEBUG):
        body = _truncate(resp.text, 3000)
        LOG.debug("HTTP %s %s%s body=%s", method, url, extra, body)
    else:
        LOG.info("HTTP %s %s%s", method, url, extra)


def _default_music_path() -> Path:
    home = Path.home()
    # Windows / macOS / Linux Downloads
    for name in ("Desert Covenant.mp3", "desert covenant.mp3"):
        p = home / "Downloads" / name
        if p.is_file():
            return p
    return home / "Downloads" / "Desert Covenant.mp3"


def _ensure_api_auth(client: httpx.Client, base: str, args: argparse.Namespace) -> dict[str, str]:
    """POST /v1/auth/login when needed; session cookie is stored on ``client`` (no Bearer headers)."""
    url_cfg = f"{base}/v1/auth/config"
    LOG.info("Checking auth: GET %s", url_cfg)
    cr = client.get(url_cfg, timeout=30.0)
    _log_response("GET", url_cfg, cr)
    if cr.status_code >= 400:
        raise SystemExit(1)
    cfg = cr.json() if cr.content else {}
    auth_on = bool((cfg.get("data") or {}).get("auth_enabled"))
    LOG.info("auth_enabled=%s", auth_on)
    if not auth_on:
        return {}

    email = (args.login_email or os.environ.get("DIRECTOR_TEST_EMAIL", "")).strip()
    pw = ((args.login_password or "") or os.environ.get("DIRECTOR_TEST_PASSWORD", "") or "").strip()
    if email and pw:
        url_login = f"{base}/v1/auth/login"
        LOG.info("Logging in: POST %s (email=%s)", url_login, email)
        lr = client.post(
            url_login,
            json={"email": email, "password": pw},
            timeout=60.0,
        )
        _log_response("POST", url_login, lr, ok_body=lr.status_code < 400)
        if lr.status_code >= 400:
            raise SystemExit(1)
        d = (lr.json().get("data") or {}) if lr.content else {}
        tid = (d.get("tenant_id") or "").strip()
        if not tid:
            LOG.error("Login response missing tenant_id. data keys=%s", list(d.keys()))
            raise SystemExit(1)
        cookies = list(client.cookies.jar)
        LOG.info(
            "Session established for %s workspace=%s… cookies=%s",
            d.get("email", email),
            tid[:8],
            len(cookies),
        )
        if LOG.isEnabledFor(logging.DEBUG):
            for c in cookies:
                LOG.debug("cookie %s domain=%s path=%s", c.name, getattr(c, "domain", ""), getattr(c, "path", ""))
        return {}

    LOG.error(
        "API has DIRECTOR_AUTH_ENABLED=true but no session credentials. "
        "Use --login-email / --login-password (or DIRECTOR_TEST_EMAIL / DIRECTOR_TEST_PASSWORD), "
        "or set DIRECTOR_ADMIN_API_KEY / --admin-key for admin enqueue + poll."
    )
    raise SystemExit(1)


def run_budget_pipeline(argv: Sequence[str] | None = None) -> int:
    """Run the budget pipeline (same as CLI). Pass ``argv`` without the script name, or ``None`` for ``sys.argv``."""
    ap = argparse.ArgumentParser(description="Budget pipeline test: auto / hands-off without fal; uses workspace TTS for narration.")
    ap.add_argument(
        "--api-base",
        default=os.environ.get("DIRECTOR_API_BASE", "http://127.0.0.1:8000").rstrip("/"),
        help="FastAPI base URL (no trailing slash).",
    )
    ap.add_argument(
        "--mode",
        choices=("auto", "hands-off"),
        default="hands-off",
        help="hands-off = unattended full_video; auto = full_video without unattended flag.",
    )
    ap.add_argument(
        "--production-media",
        action="store_true",
        help=(
            "Match Studio production brief: omit placeholder/local_ffmpeg on the project brief (use "
            "workspace Settings providers) and set auto_generate_scene_videos true. Costs real image/video API usage."
        ),
    )
    ap.add_argument(
        "--music-path",
        type=Path,
        default=None,
        help=f"Audio file to register as music bed (default: {_default_music_path()!s}).",
    )
    ap.add_argument("--title", default="Budget pipeline smoke test", help="Project title.")
    ap.add_argument(
        "--topic",
        default="A very short test documentary about local coffee shops. Keep chapters small.",
        help="Brief topic (still goes through the real text LLM).",
    )
    ap.add_argument("--runtime", type=int, default=5, help="target_runtime_minutes (2–120).")
    ap.add_argument(
        "--frame-aspect-ratio",
        choices=("16:9", "9:16"),
        default="16:9",
        help='Delivery frame: 16:9 landscape or 9:16 portrait (matches Studio brief / admin budget test).',
    )
    ap.add_argument("--poll-sec", type=float, default=4.0, help="Interval when polling agent run status.")
    ap.add_argument("--max-wait-sec", type=float, default=7200.0, help="Give up after this many seconds.")
    ap.add_argument(
        "--skip-music-upload",
        action="store_true",
        help="Do not upload a music bed (final mux may omit music).",
    )
    ap.add_argument(
        "--login-email",
        default=None,
        help="With password: POST /v1/auth/login before agent run (when auth is enabled).",
    )
    ap.add_argument(
        "--login-password",
        default=None,
        help="Password for --login-email (avoid: use DIRECTOR_TEST_PASSWORD env).",
    )
    ap.add_argument(
        "--admin-key",
        default=None,
        help="X-Director-Admin-Key for /v1/admin/budget-pipeline-test + /v1/admin/agent-runs polling. Env: DIRECTOR_ADMIN_API_KEY.",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging (every poll + response snippets).")
    ap.add_argument(
        "--log-level",
        default=os.environ.get("BUDGET_PIPELINE_LOG_LEVEL", "INFO"),
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Log level (default INFO; env BUDGET_PIPELINE_LOG_LEVEL overrides unless -v).",
    )
    ns = ap.parse_args(argv if argv is not None else None)
    level = "DEBUG" if ns.verbose else str(ns.log_level).upper()
    _configure_logging(level)

    args = ns
    admin_key = ((args.admin_key or "") or (os.environ.get("DIRECTOR_ADMIN_API_KEY") or "")).strip()
    skip_music = bool(args.skip_music_upload)
    if admin_key and not skip_music:
        LOG.warning("Admin-key mode has no session; skipping music upload.")
        skip_music = True

    music_path = Path(args.music_path) if args.music_path else _default_music_path()
    if not skip_music and not music_path.is_file():
        LOG.error("Music file not found: %s (use --music-path or --skip-music-upload)", music_path)
        return 1

    base = args.api_base.rstrip("/")
    LOG.info(
        "api_base=%s mode=%s production_media=%s skip_music=%s admin_key=%s",
        base,
        args.mode,
        args.production_media,
        skip_music,
        "set" if admin_key else "no",
    )

    pipeline_options: dict[str, Any] = {
        "through": "full_video",
        "narration_granularity": "scene",
        "auto_generate_scene_videos": True if args.production_media else False,
    }
    if args.mode == "hands-off":
        pipeline_options["unattended"] = True

    brief: dict[str, Any] = {
        "title": args.title,
        "topic": args.topic,
        "target_runtime_minutes": max(2, min(120, int(args.runtime))),
        "audience": "general",
        "tone": "documentary",
        "narration_style": "preset:narrative_documentary",
        "visual_style": "preset:cinematic_documentary",
        "frame_aspect_ratio": str(args.frame_aspect_ratio),
    }
    if not args.production_media:
        brief["preferred_image_provider"] = "placeholder"
        brief["preferred_video_provider"] = "local_ffmpeg"

    body: dict[str, Any] = {"brief": brief, "pipeline_options": pipeline_options}
    LOG.debug("agent-runs body=%s", json.dumps(body, indent=2)[:8000])

    admin_body: dict[str, Any] = {
        "title": args.title,
        "topic": args.topic,
        "target_runtime_minutes": max(2, min(120, int(args.runtime))),
        "mode": args.mode,
        "frame_aspect_ratio": str(args.frame_aspect_ratio),
        "production_media": bool(args.production_media),
    }
    tid_env = (os.environ.get("DIRECTOR_API_TENANT_ID") or "").strip()
    if tid_env:
        try:
            uuid.UUID(tid_env)
        except ValueError:
            LOG.warning("Ignoring DIRECTOR_API_TENANT_ID (not a valid workspace UUID).")
        else:
            admin_body["tenant_id"] = tid_env
    LOG.debug("admin budget-pipeline-test body=%s", json.dumps(admin_body, indent=2)[:4000])

    url_runs = f"{base}/v1/agent-runs"
    url_admin_budget = f"{base}/v1/admin/budget-pipeline-test"
    with httpx.Client(timeout=120.0) as client:
        if admin_key:
            hdr = {"X-Director-Admin-Key": admin_key}
            LOG.info("Queueing agent run (admin): POST %s", url_admin_budget)
            r = client.post(url_admin_budget, headers={**hdr, "Content-Type": "application/json"}, json=admin_body)
            _log_response("POST", url_admin_budget, r, ok_body=r.status_code < 400)
        else:
            hdr = _ensure_api_auth(client, base, args)
            LOG.info("Queueing agent run: POST %s", url_runs)
            r = client.post(url_runs, headers={**hdr, "Content-Type": "application/json"}, json=body)
            _log_response("POST", url_runs, r, ok_body=r.status_code < 400)
        if r.status_code >= 400:
            return 1
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            LOG.exception("Invalid JSON from enqueue response: %s", e)
            return 1
        run = data.get("data", {}).get("agent_run") or {}
        proj = data.get("data", {}).get("project") or {}
        run_id = run.get("id")
        project_id = proj.get("id")
        if not run_id or not project_id:
            LOG.error("Unexpected enqueue response structure: %s", _truncate(json.dumps(data, indent=2), 4000))
            return 1
        LOG.info("agent_run_id=%s project_id=%s", run_id, project_id)

        if not skip_music:
            sz = music_path.stat().st_size
            LOG.info("Uploading music bed %s (%d bytes)", music_path.name, sz)
            with music_path.open("rb") as f:
                files = {"file": (music_path.name, f, "audio/mpeg")}
                form = {
                    "title": "Budget test bed",
                    "license_or_source_ref": f"Local test file: {music_path.name} (not for distribution)",
                }
                um_url = f"{base}/v1/projects/{project_id}/music-beds/upload"
                um = client.post(um_url, headers=hdr, data=form, files=files)
            _log_response("POST", um_url, um, ok_body=um.status_code < 400)
            if um.status_code >= 400:
                return 1
            LOG.info("Music bed registered.")

        deadline = time.monotonic() + float(args.max_wait_sec)
        poll = max(1.0, float(args.poll_sec))
        last_step = None
        poll_url = (
            f"{base}/v1/admin/agent-runs/{run_id}"
            if admin_key
            else f"{base}/v1/agent-runs/{run_id}"
        )
        tick = 0
        while time.monotonic() < deadline:
            tick += 1
            gr = client.get(poll_url, headers=hdr)
            if gr.status_code >= 400:
                _log_response("GET", poll_url, gr)
                return 1
            try:
                payload = gr.json().get("data") or {}
            except json.JSONDecodeError as e:
                LOG.exception("Poll invalid JSON: %s", e)
                return 1
            status = payload.get("status")
            step = payload.get("current_step")
            if step != last_step:
                LOG.info("poll #%d status=%s step=%s (was step=%s)", tick, status, step, last_step)
                last_step = step
            elif LOG.isEnabledFor(logging.DEBUG):
                LOG.debug("poll #%d status=%s step=%s", tick, status, step)
            if status in ("succeeded", "failed", "cancelled", "blocked"):
                err = payload.get("error_message")
                if err:
                    LOG.warning("terminal status=%s error_message=%s", status, _truncate(str(err), 2000))
                LOG.info("final payload excerpt: %s", _truncate(json.dumps(payload, indent=2), 8000))
                if status == "succeeded":
                    LOG.info("Budget pipeline finished successfully.")
                    return 0
                LOG.error("Budget pipeline ended with status=%s", status)
                return 1
            time.sleep(poll)

        LOG.error("Timed out after %.0fs (poll every %.1fs). Last step=%s", args.max_wait_sec, poll, last_step)
        return 1


def main() -> int:
    return run_budget_pipeline(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
