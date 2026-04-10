from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    topic: str = Field(..., min_length=1, max_length=8000)
    target_runtime_minutes: int = Field(..., ge=2, le=120)
    audience: str | None = None
    tone: str | None = None
    visual_style: str | None = None
    narration_style: str | None = None
    factual_strictness: Literal["strict", "balanced", "creative"] | None = None
    music_preference: str | None = None
    budget_limit: float | None = None
    preferred_text_provider: str | None = None
    preferred_image_provider: str | None = None
    preferred_video_provider: str | None = None
    preferred_speech_provider: str | None = None
    research_min_sources: int | None = Field(default=None, ge=1, le=100)

    def brief_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    topic: str | None = Field(default=None, min_length=1, max_length=8000)
    target_runtime_minutes: int | None = Field(default=None, ge=2, le=120)
    status: str | None = None
    audience: str | None = None
    tone: str | None = None
    visual_style: str | None = None
    narration_style: str | None = None
    factual_strictness: Literal["strict", "balanced", "creative"] | None = None
    music_preference: str | None = None
    budget_limit: float | None = None
    preferred_text_provider: str | None = None
    preferred_image_provider: str | None = None
    preferred_video_provider: str | None = None
    preferred_speech_provider: str | None = None
    research_min_sources: int | None = Field(default=None, ge=1, le=100)
    critic_policy_json: dict[str, Any] | None = None
    use_all_approved_scene_media: bool | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    title: str
    topic: str
    status: str
    target_runtime_minutes: int
    audience: str | None
    tone: str | None
    visual_style: str | None
    narration_style: str | None
    factual_strictness: str | None
    budget_limit: float | None
    music_preference: str | None
    preferred_text_provider: str | None
    preferred_image_provider: str | None
    preferred_video_provider: str | None
    preferred_speech_provider: str | None
    workflow_phase: str
    research_min_sources: int
    director_output_json: dict[str, Any] | None
    critic_policy_json: dict[str, Any] | None = None
    use_all_approved_scene_media: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator("research_min_sources", mode="before")
    @classmethod
    def research_min_default(cls, v: Any) -> Any:
        if v is None:
            return 3
        return v

    @field_validator("director_output_json", "critic_policy_json", mode="before")
    @classmethod
    def jsonb_object_or_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        return None


class JobCreate(BaseModel):
    type: str = Field(..., pattern="^adapter_smoke$")
    provider: str = Field(..., description="openai | lm_studio | openrouter | fal | gemini")
    project_id: UUID | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    provider: str | None
    project_id: UUID | None
    payload: dict[str, Any] | None
    result: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
