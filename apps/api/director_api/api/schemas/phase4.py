from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SceneCritiqueBody(BaseModel):
    prior_report_id: UUID | None = None


class CriticWaiveBody(BaseModel):
    actor_user_id: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=8, max_length=8000)
    ticket_url: str | None = Field(default=None, max_length=2048)


class ChapterGateWaiveBody(CriticWaiveBody):
    """Same fields as scene waiver; ticket_url stored on chapter gate waiver."""


class RevisionIssuePatch(BaseModel):
    status: Literal["open", "in_progress", "resolved", "waived"] | None = None
    waiver_actor_id: str | None = Field(default=None, max_length=256)
    waiver_reason: str | None = Field(default=None, max_length=8000)


class RevisionIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    critic_report_id: UUID
    scene_id: UUID | None
    asset_id: UUID | None
    code: str
    severity: str
    message: str
    refs_json: dict[str, Any] | list[Any] | None = None
    status: str
    waiver_actor_id: str | None = None
    waiver_reason: str | None = None
    waiver_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CriticReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    target_type: str
    target_id: UUID
    job_id: UUID | None
    score: float
    passed: bool
    dimensions_json: dict[str, Any] | None = None
    issues_json: list[Any] | dict[str, Any] | None = None
    recommendations_json: list[Any] | None = None
    continuity_json: dict[str, Any] | None = None
    baseline_score: float | None = None
    prior_report_id: UUID | None = None
    meta_json: dict[str, Any] | None = None
    created_at: datetime
