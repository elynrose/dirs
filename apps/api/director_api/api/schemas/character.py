from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    sort_order: int
    name: str
    role_in_story: str
    visual_description: str
    time_place_scope_notes: str | None
    created_at: datetime
    updated_at: datetime


class ProjectCharacterCreate(BaseModel):
    name: str = Field(default="New character", min_length=1, max_length=256)
    role_in_story: str = Field(default="", max_length=2000)
    visual_description: str = Field(default="", max_length=8000)
    time_place_scope_notes: str | None = Field(default=None, max_length=2000)


class ProjectCharacterPatch(BaseModel):
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    name: str | None = Field(default=None, min_length=1, max_length=256)
    role_in_story: str | None = Field(default=None, max_length=2000)
    visual_description: str | None = Field(default=None, max_length=8000)
    time_place_scope_notes: str | None = Field(default=None, max_length=2000)
