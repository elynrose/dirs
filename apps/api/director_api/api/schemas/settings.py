from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AppSettingsPatch(BaseModel):
    config: dict[str, Any]


class AppSettingsOut(BaseModel):
    id: UUID
    tenant_id: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
