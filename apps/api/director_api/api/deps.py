import structlog
from fastapi import Depends
from sqlalchemy.orm import Session

from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.config import Settings, get_settings
from director_api.db.session import get_db
from director_api.services.runtime_settings import resolve_runtime_settings


def settings_dep(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(auth_context_dep),
) -> Settings:
    return resolve_runtime_settings(db, get_settings(), auth.tenant_id)


def meta_dep() -> dict[str, str]:
    ctx = structlog.contextvars.get_contextvars()
    return {"request_id": str(ctx.get("request_id", ""))}
