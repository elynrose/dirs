#!/usr/bin/env python3
"""Poll GET /v1/agent-runs/{id} until terminal."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import httpx

API_BASE = "http://127.0.0.1:8000"
POLL_SEC = 4.0
TIMEOUT_MIN = 90


def main() -> int:
    run_id = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not run_id:
        print("usage: poll_agent_run.py <agent_run_id>", file=sys.stderr)
        return 2

    deadline = time.time() + TIMEOUT_MIN * 60
    last: str | None = None
    with httpx.Client(base_url=API_BASE, timeout=30.0) as client:
        while time.time() < deadline:
            pr = client.get(f"/v1/agent-runs/{run_id}")
            pr.raise_for_status()
            row = pr.json().get("data") or {}
            status = str(row.get("status") or "")
            step = row.get("current_step")
            steps = row.get("steps_json") or []
            tail = steps[-1] if steps else {}
            tail_s = f"{tail.get('step')}:{tail.get('status')}" if tail else "—"
            if status != last:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"[{ts}] status={status} step={step!r} tail={tail_s}")
                last = status
            if status in ("succeeded", "failed", "cancelled", "blocked"):
                err = row.get("error_message")
                if err:
                    print(f"error_message: {str(err)[:800]}")
                print("\nTerminal step outcomes:")
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
                        print(f"  {ev.get('step')}: {st}")
                return 0 if status == "succeeded" else 1
            time.sleep(POLL_SEC)

    print(f"Timeout after {TIMEOUT_MIN} minutes", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
