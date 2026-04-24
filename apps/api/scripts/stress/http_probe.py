#!/usr/bin/env python3
"""Probe a running API (health, optional login). For manual / CI against a live stack.

Environment::

    STRESS_API_BASE_URL   default http://127.0.0.1:8000
    STRESS_EMAIL          optional — with STRESS_PASSWORD tries POST /v1/auth/login
    STRESS_PASSWORD

Examples::

    python scripts/stress/http_probe.py
    STRESS_EMAIL=u@example.com STRESS_PASSWORD='…' python scripts/stress/http_probe.py
"""

from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    base = os.environ.get("STRESS_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    email = os.environ.get("STRESS_EMAIL", "").strip()
    password = os.environ.get("STRESS_PASSWORD", "")

    with httpx.Client(timeout=30.0, base_url=base) as client:
        h = client.get("/v1/health")
        print("GET /v1/health", h.status_code, h.text[:200])
        if h.status_code != 200:
            return 1

        cfg = client.get("/v1/auth/config")
        print("GET /v1/auth/config", cfg.status_code)
        if cfg.status_code != 200:
            return 1
        auth_on = (cfg.json().get("data") or {}).get("auth_enabled")
        print("  auth_enabled:", auth_on)

        if not email or not password:
            print("Skip login (set STRESS_EMAIL and STRESS_PASSWORD)")
            return 0

        login = client.post(
            "/v1/auth/login",
            json={"email": email, "password": password},
        )
        print("POST /v1/auth/login", login.status_code)
        if login.status_code != 200:
            print(login.text[:500])
            return 1

        me = client.get("/v1/settings/usage-summary?days=7")
        print("GET /v1/settings/usage-summary (session)", me.status_code)
        if me.status_code not in (200, 401, 403):
            print(me.text[:300])
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
