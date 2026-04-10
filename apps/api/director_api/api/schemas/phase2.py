from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchApproveBody(BaseModel):
    notes: str | None = Field(default=None, max_length=8000)


class ResearchOverrideBody(BaseModel):
    actor_user_id: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=8, max_length=8000)
    ticket_url: str | None = Field(default=None, max_length=2048)


class ResearchDossierBodyPatch(BaseModel):
    """Replace the latest dossier JSON body (must satisfy research-dossier.schema.json)."""

    body: dict[str, Any]


class ChapterScriptPatch(BaseModel):
    script_text: str = Field(..., min_length=1)


class ChapterScriptRegenerateBody(BaseModel):
    """Notes used to steer a single-chapter script LLM pass (e.g. chapter summary / editorial direction)."""

    enhancement_notes: str = Field(..., min_length=8, max_length=16_000)


class ChapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    order_index: int
    title: str
    summary: str | None
    target_duration_sec: int | None
    script_text: str | None
    status: str
    critic_gate_status: str | None = None
    critic_gate_waived_at: datetime | None = None
    critic_gate_waiver_actor_id: str | None = None
    pacing_warning: str | None = None


class ChapterPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    summary: str | None = Field(default=None, max_length=80000)
    target_duration_sec: int | None = Field(default=None, ge=30, le=7200)
    script_text: str | None = Field(default=None, max_length=120_000)

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if (
            self.title is None
            and self.summary is None
            and self.target_duration_sec is None
            and self.script_text is None
        ):
            raise ValueError("at least one of title, summary, target_duration_sec, script_text is required")
        return self
