from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
import jwt

from director_api.config import Settings

_WEAK_JWT_SECRETS = frozenset(
    {
        "change-me",
        "changeme",
        "secret",
        "director-dev-secret",
        "dev",
        "test",
        "password",
        "director",
    }
)


def jwt_secret_is_weak(secret: str | None) -> bool:
    """True if the secret is missing, too short, or a common placeholder."""
    s = (secret or "").strip()
    if len(s) < 16:
        return True
    if s.lower() in _WEAK_JWT_SECRETS:
        return True
    return False


def issue_access_token(
    *,
    settings: Settings,
    user_id: int,
    tenant_id: str | None = None,
) -> str:
    if not (settings.director_jwt_secret or "").strip():
        raise RuntimeError("DIRECTOR_JWT_SECRET is required when auth is enabled")
    now = datetime.now(tz=UTC)
    exp = now + timedelta(hours=max(1, int(settings.director_jwt_expire_hours)))
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": exp,
    }
    tid = (tenant_id or "").strip()
    if tid:
        payload["tid"] = tid
    return jwt.encode(payload, settings.director_jwt_secret, algorithm="HS256")


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    if not (settings.director_jwt_secret or "").strip():
        raise RuntimeError("DIRECTOR_JWT_SECRET is required when auth is enabled")
    return jwt.decode(token, settings.director_jwt_secret, algorithms=["HS256"])
