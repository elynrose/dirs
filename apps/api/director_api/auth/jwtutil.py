from __future__ import annotations

_WEAK_SECRETS = frozenset(
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
    """True if the symmetric signing secret is missing, too short, or a common placeholder.

    Used for ``DIRECTOR_JWT_SECRET`` (YouTube OAuth state HMAC and similar), not user access tokens.
    """
    s = (secret or "").strip()
    if len(s) < 16:
        return True
    if s.lower() in _WEAK_SECRETS:
        return True
    return False
