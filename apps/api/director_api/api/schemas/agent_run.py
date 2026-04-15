from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from director_api.api.schemas.project import ProjectCreate


class AgentRunCreate(BaseModel):
    """Start a full autonomous Phase 2 pipeline. Provide either `project_id` or `brief` (not both)."""

    project_id: UUID | None = None
    brief: ProjectCreate | None = None
    # continue_from_existing: skip steps already satisfied (project workflow_phase + data). For ``project_id`` runs it
    # defaults to true on the server when omitted so Auto/Automate does not redo research, scripts, or media that
    # already exists; pass false explicitly to force a full re-walk (rare).
    # through: "chapters" stops after chapter scripts (manual new-run default); "critique" stops after scene planning
    # (worker runs a one-time story-vs-research LLM check after scenes on first pass, then never repeats);
    # "full_video" runs character bible, scene images, TTS, timeline, rough+final cut after scenes (story check once per project as above).
    # unattended: true relaxes the strict research source gate (warn-only) so a run can finish without human dossier fixes.
    # Server-side parsing treats unattended as full depth: missing ``through`` defaults to full_video, and ``through: critique``
    # is coerced to full_video so hands-off runs do not stop after story vs research.
    # force_replan_scenes: replan every scripted chapter even if workflow_phase already has scenes_planned.
    # auto_generate_scene_videos: when true and the run reaches the full-video tail, generate scene videos for scenes missing one
    # (overrides workspace default for this run if set).
    # rerun_from_step: optional canonical phase to re-execute (implies continue_from_existing); merges with oversight so earlier
    # structural gaps still run first. Values: director, research, outline, chapters, scenes,
    # auto_characters, auto_images, auto_videos, auto_narration, auto_timeline, auto_rough_cut, auto_final_cut. Tail steps need through: full_video.
    # narration_granularity: "scene" (default for full_video) runs per-scene TTS in the automation tail; "chapter" keeps one file per chapter.
    # force_pipeline_steps: list of canonical steps to execute even when continue_from_existing would fast-skip (e.g. full restart).
    # Include "scenes" to replan scenes; tail steps auto_characters / auto_images / auto_videos / auto_narration also regenerate existing assets when listed.
    # With through: full_video, timeline + rough + final cut still run after the media tail (not individually skippable yet).
    # rerun_web_research: optional bool. False = skip the Tavily/web research step when a dossier already exists (overrides oversight for that
    # step unless "research" is in force_pipeline_steps). True = always execute research. Omitted = default skip rules + oversight only.
    pipeline_options: dict[str, Any] | None = None

    @model_validator(mode="after")
    def check_project_or_brief(self) -> Self:
        if self.project_id is not None and self.brief is not None:
            raise ValueError("provide either project_id or brief, not both")
        if self.project_id is None and self.brief is None:
            raise ValueError("provide project_id or brief")
        return self


class AgentRunListItem(BaseModel):
    """Compact row for project history lists (no `steps_json` payload)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    status: str
    current_step: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    started_by_user_id: int | None = None
    status: str
    current_step: str | None
    steps_json: list[Any]
    block_code: str | None
    block_message: str | None
    block_detail_json: dict[str, Any] | None
    pipeline_options_json: dict[str, Any] | None = None
    pipeline_control_json: dict[str, Any] | None = None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AgentRunPipelineControl(BaseModel):
    """Pause, resume, or stop the autonomous pipeline worker (honored at step boundaries)."""

    action: Literal["pause", "resume", "stop"] = Field(
        ...,
        description="pause: request hold at next checkpoint; resume: clear pause; stop: cancel run when safe",
    )
