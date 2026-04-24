"""API schemas for project ideas (topic → titles/descriptions) and scheduled runs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IdeaGenerateIn(BaseModel):
    topic: str = Field(..., min_length=2, max_length=4000)


class IdeaItem(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1, max_length=8000)


class IdeaGenerateOut(BaseModel):
    ideas: list[IdeaItem]


class ProjectIdeaCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1, max_length=8000)
    source_topic: str = Field(..., min_length=1, max_length=4000)


class ProjectIdeaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_topic: str
    title: str
    description: str
    created_at: datetime
    updated_at: datetime


class IdeaRunIn(BaseModel):
    target_runtime_minutes: int = Field(default=10, ge=2, le=120)


class IdeaInstantRunIn(BaseModel):
    """Start the pipeline from a title/description without persisting a saved idea row."""

    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1, max_length=8000)
    target_runtime_minutes: int = Field(default=10, ge=2, le=120)


class IdeaScheduleIn(BaseModel):
    """ISO 8601 datetime with offset (e.g. from ``datetime-local`` + timezone)."""

    scheduled_at: datetime


class IdeaScheduledRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    idea_id: UUID
    scheduled_at: datetime
    status: str
    agent_run_id: UUID | None
    error_message: str | None
    created_at: datetime
