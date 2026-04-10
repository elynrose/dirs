import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import IdempotencyRecord


def require_idempotency_key(raw: str | None) -> str:
    if not raw or len(raw) < 8:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": "Idempotency-Key header required (min 8 chars)"},
        )
    return raw


def body_hash(body_dict: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(body_dict, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def idempotency_replay_or_conflict(
    db: Session,
    *,
    tenant_id: str,
    route: str,
    key: str,
    h: str,
) -> JSONResponse | None:
    existing = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.route == route,
            IdempotencyRecord.key == key,
        )
    ).scalar_one_or_none()
    if not existing:
        return None
    if existing.body_hash != h:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": "same key, different body"},
        )
    return JSONResponse(status_code=existing.response_status, content=existing.response_body)


def store_idempotency(
    db: Session,
    *,
    tenant_id: str,
    route: str,
    key: str,
    h: str,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    rec = IdempotencyRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        route=route,
        key=key,
        body_hash=h,
        response_status=response_status,
        response_body=response_body,
    )
    db.add(rec)
    db.commit()
