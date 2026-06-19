"""Workspace settings request/response schemas.

YouTube integration keys in ``config`` (also available via env):
``youtube_client_id``, ``youtube_client_secret``, ``youtube_refresh_token`` (OAuth callback),
``youtube_auto_upload_after_export``, ``youtube_default_privacy``, ``youtube_share_watch_link_in_telegram``.
Requires ``public_api_base_url`` for OAuth redirect URI.
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AppSettingsPatch(BaseModel):
    config: dict[str, Any]


class AppSettingsOut(BaseModel):
    id: UUID
    tenant_id: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    platform_credential_keys_inherited: list[str] = Field(default_factory=list)
    credential_keys_present: dict[str, bool] = Field(
        default_factory=dict,
        description="True when a non-empty secret is stored for that key (values are omitted from config).",
    )
