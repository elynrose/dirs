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
