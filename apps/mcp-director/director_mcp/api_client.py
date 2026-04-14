"""HTTP client for Directely REST API (used by MCP tools)."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


class DirectorApiError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body[:2000]}")


class DirectorApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None = None,
        tenant_id: str | None = None,
        timeout_sec: float = 120.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = (bearer_token or "").strip() or None
        self._tenant = (tenant_id or "").strip() or None
        self._timeout = timeout_sec

    @classmethod
    def from_env(cls) -> DirectorApiClient:
        raw = (os.environ.get("DIRECTOR_API_BASE_URL") or "http://127.0.0.1:8000").strip().rstrip("/")
        token = (os.environ.get("DIRECTOR_API_TOKEN") or os.environ.get("DIRECTOR_JWT") or "").strip() or None
        tenant = (os.environ.get("DIRECTOR_TENANT_ID") or os.environ.get("DIRECTOR_DEFAULT_TENANT_ID") or "").strip() or None
        timeout = float(os.environ.get("DIRECTOR_HTTP_TIMEOUT_SEC") or "120")
        return cls(base_url=raw, bearer_token=token, tenant_id=tenant, timeout_sec=timeout)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self._tenant:
            h["X-Tenant-Id"] = self._tenant
        return h

    def _url(self, path: str) -> str:
        p = path if path.startswith("/") else f"/{path}"
        return f"{self._base}{p}"

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self._timeout) as client:
            r = client.request(
                method.upper(),
                self._url(path),
                headers=self._headers(),
                content=json.dumps(json_body) if json_body is not None else None,
            )
        text = r.text or ""
        if not r.is_success:
            raise DirectorApiError(r.status_code, text)
        try:
            out = r.json()
        except json.JSONDecodeError as exc:
            raise DirectorApiError(r.status_code, text or "invalid JSON") from exc
        return out if isinstance(out, dict) else {"value": out}

    def post_v1(self, subpath: str, body: dict[str, Any]) -> dict[str, Any]:
        p = subpath if subpath.startswith("/v1/") else f"/v1/{subpath.lstrip('/')}"
        return self.request_json("POST", p, json_body=body)

    def get_v1(self, subpath: str) -> dict[str, Any]:
        p = subpath if subpath.startswith("/v1/") else f"/v1/{subpath.lstrip('/')}"
        return self.request_json("GET", p, json_body=None)
