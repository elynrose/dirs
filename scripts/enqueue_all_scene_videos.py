#!/usr/bin/env python3
"""
Enqueue POST /v1/scenes/{id}/generate-video for every scene in a project (all chapters).

Uses only the stdlib. Requires API reachable (default http://127.0.0.1:8000).

  DIRECTOR_API_BASE=http://127.0.0.1:8000 PROJECT_ID=<uuid> python3 scripts/enqueue_all_scene_videos.py
  python3 scripts/enqueue_all_scene_videos.py --dry-run

When you see HTTP 429 (tenant media cap, default 3 concurrent), use --wait to poll until
slots free up, or raise job_cap_media via PATCH /v1/settings (merged into runtime config).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request


def _request(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict | list | str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def main() -> int:
    p = argparse.ArgumentParser(description="Enqueue scene_generate_video for all scenes in a project.")
    p.add_argument("--base", default=os.environ.get("DIRECTOR_API_BASE", "http://127.0.0.1:8000").rstrip("/"))
    p.add_argument("--project-id", default=os.environ.get("PROJECT_ID", "").strip(), help="UUID; default: first project")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="On 429, sleep and retry until queued (default: true). Use --no-wait to fail fast.",
    )
    p.add_argument("--poll-sec", type=float, default=4.0, help="Seconds between retries after 429")
    p.add_argument(
        "--skip-if-video-exists",
        action="store_true",
        help="Skip scenes that already have a video asset in running or succeeded (safe for re-runs).",
    )
    args = p.parse_args()
    base = args.base

    pid = args.project_id
    if not pid:
        code, body = _request("GET", f"{base}/v1/projects?limit=100")
        if code != 200 or not isinstance(body, dict):
            print(f"Failed to list projects: HTTP {code} {body}", file=sys.stderr)
            return 1
        projects = (body.get("data") or {}).get("projects") or []
        if not projects:
            print("No projects found.", file=sys.stderr)
            return 1
        pid = projects[0]["id"]
        title = projects[0].get("title") or ""
        print(f"Using first project {pid} {title!r}")

    code, body = _request("GET", f"{base}/v1/projects/{pid}/chapters")
    if code != 200 or not isinstance(body, dict):
        print(f"Failed to list chapters: HTTP {code} {body}", file=sys.stderr)
        return 1
    chapters = (body.get("data") or {}).get("chapters") or []
    if not chapters:
        print("No chapters on this project.", file=sys.stderr)
        return 1

    scenes: list[tuple[str, str, int]] = []
    for ch in sorted(chapters, key=lambda c: c.get("order_index", 0)):
        cid = ch["id"]
        cidx = int(ch.get("order_index", 0)) + 1
        code, body = _request("GET", f"{base}/v1/chapters/{cid}/scenes")
        if code != 200 or not isinstance(body, dict):
            print(f"Failed to list scenes for chapter {cid}: HTTP {code}", file=sys.stderr)
            return 1
        for s in (body.get("data") or {}).get("scenes") or []:
            scenes.append((str(s["id"]), str(cid), cidx))

    if not scenes:
        print("No scenes found across chapters.", file=sys.stderr)
        return 1

    print(f"Found {len(scenes)} scene(s) across {len(chapters)} chapter(s).")
    if args.dry_run:
        for sid, _cid, cidx in scenes:
            print(f"  would enqueue video: scene {sid} (chapter {cidx})")
        return 0

    ok = 0
    fail = 0
    skipped = 0
    for sid, _cid, cidx in scenes:
        if args.skip_if_video_exists:
            code_a, ab = _request("GET", f"{base}/v1/scenes/{sid}/assets")
            if code_a == 200 and isinstance(ab, dict):
                assets = (ab.get("data") or {}).get("assets") or []
                busy = {"running", "succeeded"}
                if any(
                    str(a.get("asset_type") or "").lower() == "video" and str(a.get("status") or "") in busy
                    for a in assets
                ):
                    print(f"  ch{cidx} scene {sid[:8]}… skip (video running or done)")
                    skipped += 1
                    continue
        ik = str(uuid.uuid4())
        attempts = 0
        max_attempts = 2000
        while attempts < max_attempts:
            attempts += 1
            code, body = _request(
                "POST",
                f"{base}/v1/scenes/{sid}/generate-video",
                body={"generation_tier": "preview"},
                idempotency_key=ik,
            )
            if code in (200, 202):
                job = (body.get("job") if isinstance(body, dict) else None) or {}
                jid = job.get("id", "?")
                print(f"  ch{cidx} scene {sid[:8]}… → job {str(jid)[:8]}… HTTP {code}")
                ok += 1
                break
            detail = body.get("detail") if isinstance(body, dict) else None
            code_cap = isinstance(detail, dict) and detail.get("code") in ("JOB_CAPACITY", "JOB_CAPACITY_GLOBAL_MEDIA")
            cap = code == 429 and code_cap
            if cap and args.wait:
                if attempts == 1 or attempts % 15 == 0:
                    print(f"  ch{cidx} scene {sid[:8]}… cap full, waiting {args.poll_sec}s…", file=sys.stderr)
                time.sleep(args.poll_sec)
                continue
            msg = body
            if isinstance(body, dict):
                msg = body.get("detail") or body.get("error") or body
            print(f"  ch{cidx} scene {sid[:8]}… FAILED HTTP {code}: {msg}", file=sys.stderr)
            fail += 1
            break
        else:
            print(f"  ch{cidx} scene {sid[:8]}… gave up after {max_attempts} waits", file=sys.stderr)
            fail += 1

    print(f"Done: {ok} queued, {skipped} skipped, {fail} failed.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
