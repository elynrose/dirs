#!/usr/bin/env python3
"""Queue a short agent run via POST /v1/agent-runs and poll until terminal."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import httpx

API_BASE = "http://127.0.0.1:8000"
POLL_SEC = 4.0
TIMEOUT_MIN = 90


def main() -> int:
    body = {
        "brief": {
            "title": "Coffee Ritual (smoke)",
            "topic": (
                "A tight one-minute-style documentary beat: how a morning coffee ritual "
                "signals the start of the day. Single narrative arc, 2–3 implied scenes, "
                "clear VO, no tangents."
            ),
            "target_runtime_minutes": 2,
            "audience": "general",
            "tone": "documentary",
            "narration_style": "preset:narrative_documentary",
            "visual_style": "preset:cinematic_documentary",
            "frame_aspect_ratio": "16:9",
        },
        "pipeline_options": {
            "through": "critique",
            "narration_granularity": "scene",
            "auto_generate_scene_images": False,
            "auto_generate_scene_videos": False,
        },
    }

    deadline = time.time() + TIMEOUT_MIN * 60
    with httpx.Client(base_url=API_BASE, timeout=httpx.Timeout(30.0, read=7200.0)) as client:
        print("GET /v1/health …")
        hr = client.get("/v1/health")
        hr.raise_for_status()
        print(f"  health: {hr.json()}")

        print("POST /v1/agent-runs …")
        print(json.dumps(body, indent=2)[:1200])
        cr = client.post("/v1/agent-runs", json=body)
        if cr.status_code >= 400:
            print(cr.status_code, cr.text[:4000], file=sys.stderr)
            return 1
        data = cr.json().get("data") or {}
        run = data.get("agent_run") or {}
        project = data.get("project") or {}
        run_id = str(run.get("id") or "")
        project_id = str(project.get("id") or "")
        if not run_id:
            print("missing agent_run id", cr.text[:2000], file=sys.stderr)
            return 1
        print(f"  project_id={project_id}")
        print(f"  agent_run_id={run_id}")
        print(f"  poll every {POLL_SEC}s (timeout {TIMEOUT_MIN}m)")

        last_step = None
        while time.time() < deadline:
            pr = client.get(f"/v1/agent-runs/{run_id}")
            pr.raise_for_status()
            row = pr.json().get("data") or {}
            status = str(row.get("status") or "")
            step = row.get("current_step")
            steps = row.get("steps_json") or []
            tail = steps[-1] if steps else {}
            tail_s = f"{tail.get('step')}:{tail.get('status')}" if tail else "—"
            if step != last_step or status in ("succeeded", "failed", "cancelled", "blocked"):
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"  [{ts}] status={status} step={step!r} tail={tail_s}")
                last_step = step
            if status in ("succeeded", "failed", "cancelled", "blocked"):
                err = row.get("error_message")
                if err:
                    print(f"  error_message: {str(err)[:500]}")
                print("\nFinal steps (terminal outcomes):")
                seen: set[str] = set()
                for ev in steps:
                    if not isinstance(ev, dict):
                        continue
                    st = str(ev.get("status") or "")
                    if st in ("succeeded", "failed", "skipped", "partial_failed", "blocked", "cancelled"):
                        key = f"{ev.get('step')}:{st}"
                        if key in seen:
                            continue
                        seen.add(key)
                        extra = {k: v for k, v in ev.items() if k not in ("at", "step", "status") and v is not None}
                        suffix = f" {extra}" if extra else ""
                        print(f"    {ev.get('step')}: {st}{suffix}")
                return 0 if status == "succeeded" else 1
            time.sleep(POLL_SEC)

    print(f"Timeout after {TIMEOUT_MIN} minutes", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
