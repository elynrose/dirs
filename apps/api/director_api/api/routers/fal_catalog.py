"""FAL model lists from on-disk catalog (``data/media_models_catalog.json``).

Catalog is populated by ``POST /v1/fal/models/sync`` (calls fal Platform API once, merges image + video sections).
``GET /v1/fal/models`` and ``GET /v1/settings/fal-models`` read from JSON only — no live HTTP per GET.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from director_api.api.deps import auth_user_id_int, meta_dep, settings_dep
from director_api.auth.context import AuthContext
from director_api.auth.deps import auth_context_dep
from director_api.config import Settings
from director_api.db.session import get_db
from director_api.services.media_models_catalog import (
    get_fal_models_for_media,
    sync_fal_catalog_from_api,
)

router = APIRouter(tags=["fal"])


def load_fal_models_data(
    db: Session,
    settings: Settings,
    media: Literal["image", "video"],
) -> dict[str, Any]:
    """
    Returns ``{"media", "models", "fal_categories", ...}`` from ``data/media_models_catalog.json``.
    Shared by GET /v1/fal/models and GET /v1/settings/fal-models.
    """
    row = get_fal_models_for_media(media)
    return {
        "media": row["media"],
        "models": row["models"],
        "fal_categories": row["fal_categories"],
        "catalog_path": row["catalog_path"],
        "catalog_updated_at": row["catalog_updated_at"],
        "cache_age_sec": row["cache_age_sec"],
        "needs_sync": row["needs_sync"],
        "source": "disk",
    }


@router.get("/fal/models")
def list_fal_models(
    media: Literal["image", "video"] = Query(
        ...,
        description="image → text-to-image + image-to-image (merged); video → text-to-video + image-to-video (merged)",
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    meta: dict = Depends(meta_dep),
) -> JSONResponse:
    data = load_fal_models_data(db, settings, media)
    return JSONResponse(
        content={"data": data, "meta": meta},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/fal/models/sync")
def sync_fal_models(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    auth: AuthContext = Depends(auth_context_dep),
    meta: dict = Depends(meta_dep),
) -> dict:
    """Refresh ``data/media_models_catalog.json`` from the fal Platform API (image + video sections)."""
    try:
        summary = sync_fal_catalog_from_api(db, settings, user_id=auth_user_id_int(auth))
    except RuntimeError as e:
        raise HTTPException(
            status_code=502,
            detail={"code": "FAL_CATALOG_SYNC_ERROR", "message": str(e)[:800]},
        ) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"code": "FAL_CATALOG_SYNC_ERROR", "message": str(e)[:800]},
        ) from e
    return {"data": summary, "meta": meta}
