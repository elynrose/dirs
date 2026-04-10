"""Verify Firebase Auth ID tokens (e.g. Google sign-in) using firebase-admin."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from director_api.config import Settings

log = structlog.get_logger(__name__)

# director_api/services/firebase_id_token.py → repo root (director/)
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _resolved_credentials_path(settings: Settings) -> str | None:
    raw = (settings.director_firebase_credentials_path or "").strip()
    if not raw:
        return None
    p = Path(raw.replace("\\", "/"))
    if not p.is_absolute():
        rel = raw.strip().replace("\\", "/").lstrip("./")
        p = (_REPO_ROOT / rel).resolve()
    else:
        p = p.resolve()
    if p.is_file():
        return str(p)
    return None

_app_initialized = False


def firebase_web_config_for_client(settings: Settings) -> dict[str, str] | None:
    """Public fields for the Firebase JS SDK. Safe to expose without service account on disk."""
    pid = (settings.director_firebase_project_id or "").strip()
    key = (settings.director_firebase_web_api_key or "").strip()
    domain = (settings.director_firebase_web_auth_domain or "").strip()
    app_id = (settings.director_firebase_web_app_id or "").strip()
    if not key or not domain or not app_id:
        return None
    if not pid and domain.endswith(".firebaseapp.com"):
        pid = domain[: -len(".firebaseapp.com")].strip()
    if not pid:
        return None
    return {
        "api_key": key,
        "auth_domain": domain,
        "project_id": pid,
        "app_id": app_id,
    }


def firebase_sign_in_available(settings: Settings) -> bool:
    """Server can verify Firebase ID tokens (service account JSON path must exist)."""
    if firebase_web_config_for_client(settings) is None:
        return False
    return _resolved_credentials_path(settings) is not None


def _ensure_firebase_app(settings: Settings) -> None:
    global _app_initialized
    import firebase_admin
    from firebase_admin import credentials

    if _app_initialized or firebase_admin._apps:
        _app_initialized = True
        return
    cred_path = _resolved_credentials_path(settings)
    if not cred_path:
        raise RuntimeError("Firebase credentials path missing or not a file")
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
    _app_initialized = True


def verify_firebase_id_token(settings: Settings, id_token: str) -> dict[str, Any]:
    """Validate a Firebase ID token JWT and return decoded claims (includes uid, email, etc.)."""
    _ensure_firebase_app(settings)
    from firebase_admin import auth as firebase_auth

    try:
        return firebase_auth.verify_id_token(id_token, check_revoked=False)
    except Exception as exc:
        log.warning("firebase_id_token_verify_failed", error=str(exc))
        raise


def firebase_public_web_config(settings: Settings) -> dict[str, str] | None:
    """Config for Firebase JS SDK in GET /v1/auth/config (may be set before service account is deployed)."""
    return firebase_web_config_for_client(settings)
