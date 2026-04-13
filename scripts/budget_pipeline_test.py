#!/usr/bin/env python3
"""
Drive an Auto or Hands-off agent run while avoiding paid *image* APIs (fal).

Uses project-level providers for cheap visuals (no FAL_KEY needed for images/video in this path):
  - preferred_image_provider: placeholder  (solid FFmpeg lavfi frame)
  - preferred_video_provider: local_ffmpeg (still → MP4 on your machine)
  - preferred_speech_provider: omitted — narration uses the workspace default TTS from API settings
    (``active_speech_provider`` / OpenAI, Kokoro, etc.), not FFmpeg ding. Set ``DIRECTOR_PLACEHOLDER_MEDIA=1``
    on the worker to force ding for any project.

Studio parity: starting an agent run from the web app sends the same optional brief fields from
Settings → Providers (image / video / speech / text) and defaults auto scene-video generation to on
unless you explicitly disable it in Settings (matches this script’s full_video + auto_generate_scene_videos).
The full-video automation tail also builds or refreshes the character bible before scene images when needed (same as Studio).

You still need a **text** LLM path (OpenAI, LM Studio, etc.) and whatever research uses (Tavily optional);
this script does not stub those.

Prerequisites
  - API + Celery worker running, DB/Redis up (same as normal Studio).
  - ffmpeg on PATH for the worker (already required for Directely).
  - Optional: set DIRECTOR_PLACEHOLDER_MEDIA=1 on the worker to force placeholder media for *any*
    project (overrides Studio provider picks). Otherwise this script sets providers on the new project only.

Music
  - After the run is queued, uploads a local file as a music bed (default: ~/Downloads/Desert Covenant.mp3).
  - Adjust with --music-path. Use a path to any supported audio file you have.

Auth (when DIRECTOR_AUTH_ENABLED=true)
  - Preferred: log in from the script (uses default workspace as X-Tenant-Id):
      python scripts/budget_pipeline_test.py --login-email you@example.com --login-password '…'
    Password can come from env instead: DIRECTOR_TEST_PASSWORD
  - Or set tokens yourself:
      export DIRECTOR_API_BEARER='<jwt>'
      export DIRECTOR_API_TENANT_ID='<uuid>'
  - CLI overrides env: --bearer … --tenant-id …

Examples
  python scripts/budget_pipeline_test.py --mode hands-off
  python scripts/budget_pipeline_test.py --mode hands-off --login-email you@example.com
  python scripts/budget_pipeline_test.py --mode auto --api-base http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError as e:
    print("Install httpx in this interpreter (e.g. apps/api .venv: pip install httpx)", file=sys.stderr)
    raise SystemExit(2) from e


def _default_music_path() -> Path:
    home = Path.home()
    # Windows / macOS / Linux Downloads
    for name in ("Desert Covenant.mp3", "desert covenant.mp3"):
        p = home / "Downloads" / name
        if p.is_file():
            return p
    return home / "Downloads" / "Desert Covenant.mp3"


def _headers_from_env_and_args(args: argparse.Namespace) -> dict[str, str]:
    h: dict[str, str] = {}
    token = (
        (getattr(args, "bearer", None) or "").strip()
        or os.environ.get("DIRECTOR_API_BEARER", "")
        or os.environ.get("DIRECTOR_TEST_BEARER", "")
    ).strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    tid = (
        (getattr(args, "tenant_id", None) or "").strip()
        or os.environ.get("DIRECTOR_API_TENANT_ID", "")
        or os.environ.get("DIRECTOR_TEST_TENANT_ID", "")
    ).strip()
    if tid:
        h["X-Tenant-Id"] = tid
    return h


def _ensure_api_auth(client: httpx.Client, base: str, args: argparse.Namespace) -> dict[str, str]:
    """Return headers with Bearer + X-Tenant-Id when SaaS auth is on; else {}."""
    cr = client.get(f"{base}/v1/auth/config", timeout=30.0)
    if cr.status_code >= 400:
        print(f"GET /v1/auth/config failed: {cr.status_code} {cr.text[:500]}", file=sys.stderr)
        raise SystemExit(1)
    auth_on = bool((cr.json().get("data") or {}).get("auth_enabled"))
    if not auth_on:
        return {}

    h = _headers_from_env_and_args(args)
    has_authz = bool(h.get("Authorization"))
    has_tenant = bool(h.get("X-Tenant-Id"))
    if has_authz ^ has_tenant:
        print(
            "When auth is enabled, provide both a Bearer token and X-Tenant-Id "
            "(e.g. DIRECTOR_API_BEARER + DIRECTOR_API_TENANT_ID, or --bearer + --tenant-id).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if h.get("Authorization") and h.get("X-Tenant-Id"):
        return h

    email = (args.login_email or os.environ.get("DIRECTOR_TEST_EMAIL", "")).strip()
    pw = ((args.login_password or "") or os.environ.get("DIRECTOR_TEST_PASSWORD", "") or "").strip()
    if email and pw:
        lr = client.post(
            f"{base}/v1/auth/login",
            json={"email": email, "password": pw},
            timeout=60.0,
        )
        if lr.status_code >= 400:
            print(f"POST /v1/auth/login failed: {lr.status_code} {lr.text[:1500]}", file=sys.stderr)
            raise SystemExit(1)
        d = (lr.json().get("data") or {}) if lr.content else {}
        tok = (d.get("access_token") or "").strip()
        tid = (d.get("tenant_id") or "").strip()
        if not tok or not tid:
            print("Login response missing access_token or tenant_id.", file=sys.stderr)
            raise SystemExit(1)
        print(f"Logged in as {d.get('email', email)} (workspace {tid[:8]}…).")
        return {"Authorization": f"Bearer {tok}", "X-Tenant-Id": tid}

    print(
        "API has DIRECTOR_AUTH_ENABLED=true but no credentials were provided.\n\n"
        "  Option A — log in from the script:\n"
        "    python scripts/budget_pipeline_test.py --login-email YOU@MAIL --login-password '…'\n"
        "    (or set DIRECTOR_TEST_EMAIL and DIRECTOR_TEST_PASSWORD)\n\n"
        "  Option B — use an existing session token:\n"
        "    set DIRECTOR_API_BEARER=<jwt>\n"
        "    set DIRECTOR_API_TENANT_ID=<workspace-uuid>\n"
        "    (Studio: same values the web app uses; or --bearer / --tenant-id)\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> int:
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
    ap.add_argument("--poll-sec", type=float, default=4.0, help="Interval when polling agent run status.")
    ap.add_argument("--max-wait-sec", type=float, default=7200.0, help="Give up after this many seconds.")
    ap.add_argument(
        "--skip-music-upload",
        action="store_true",
        help="Do not upload a music bed (final mux may omit music).",
    )
    ap.add_argument("--bearer", default=None, help="JWT (overrides DIRECTOR_API_BEARER). Requires --tenant-id.")
    ap.add_argument("--tenant-id", default=None, dest="tenant_id", help="Workspace id (overrides DIRECTOR_API_TENANT_ID).")
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
    args = ap.parse_args()

    music_path = Path(args.music_path) if args.music_path else _default_music_path()
    if not args.skip_music_upload and not music_path.is_file():
        print(
            f"Music file not found: {music_path}\n"
            "Pass --music-path to an existing audio file or --skip-music-upload.",
            file=sys.stderr,
        )
        return 1

    base = args.api_base.rstrip("/")

    pipeline_options: dict[str, Any] = {
        "through": "full_video",
        "narration_granularity": "scene",
        "auto_generate_scene_videos": True,
    }
    if args.mode == "hands-off":
        pipeline_options["unattended"] = True

    body: dict[str, Any] = {
        "brief": {
            "title": args.title,
            "topic": args.topic,
            "target_runtime_minutes": max(2, min(120, int(args.runtime))),
            "audience": "general",
            "tone": "documentary",
            "narration_style": "preset:narrative_documentary",
            "visual_style": "preset:cinematic_documentary",
            "preferred_image_provider": "placeholder",
            "preferred_video_provider": "local_ffmpeg",
        },
        "pipeline_options": pipeline_options,
    }

    print("POST /v1/agent-runs …")
    with httpx.Client(timeout=120.0) as client:
        hdr = _ensure_api_auth(client, base, args)

        r = client.post(f"{base}/v1/agent-runs", headers={**hdr, "Content-Type": "application/json"}, json=body)
        if r.status_code >= 400:
            print(r.status_code, r.text[:4000], file=sys.stderr)
            return 1
        data = r.json()
        run = data.get("data", {}).get("agent_run") or {}
        proj = data.get("data", {}).get("project") or {}
        run_id = run.get("id")
        project_id = proj.get("id")
        if not run_id or not project_id:
            print("Unexpected response:", json.dumps(data, indent=2)[:4000])
            return 1
        print(f"  agent_run_id={run_id}")
        print(f"  project_id={project_id}")

        if not args.skip_music_upload:
            print(f"POST music bed upload ({music_path.name}) …")
            with music_path.open("rb") as f:
                files = {"file": (music_path.name, f, "audio/mpeg")}
                form = {
                    "title": "Budget test bed",
                    "license_or_source_ref": f"Local test file: {music_path.name} (not for distribution)",
                }
                um = client.post(
                    f"{base}/v1/projects/{project_id}/music-beds/upload",
                    headers=hdr,
                    data=form,
                    files=files,
                )
            if um.status_code >= 400:
                print(um.status_code, um.text[:2000], file=sys.stderr)
                return 1
            print("  music bed registered.")

        deadline = time.monotonic() + float(args.max_wait_sec)
        poll = max(1.0, float(args.poll_sec))
        last_step = None
        while time.monotonic() < deadline:
            gr = client.get(f"{base}/v1/agent-runs/{run_id}", headers=hdr)
            if gr.status_code >= 400:
                print(gr.status_code, gr.text[:2000], file=sys.stderr)
                return 1
            payload = gr.json().get("data") or {}
            status = payload.get("status")
            step = payload.get("current_step")
            if step != last_step:
                print(f"  status={status} step={step}")
                last_step = step
            if status in ("succeeded", "failed", "cancelled", "blocked"):
                print(json.dumps(payload, indent=2)[:8000])
                if status == "succeeded":
                    return 0
                return 1
            time.sleep(poll)

        print("Timed out waiting for terminal agent run status.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
