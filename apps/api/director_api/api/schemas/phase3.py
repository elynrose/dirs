from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_id: UUID
    order_index: int
    purpose: str | None
    planned_duration_sec: int | None
    narration_text: str | None
    visual_type: str | None
    prompt_package_json: dict[str, Any] | None
    continuity_tags_json: list[Any] | None
    status: str
    critic_score: float | None
    critic_revision_count: int | None = None
    critic_passed: bool | None = None
    critic_waived_at: datetime | None = None
    critic_waiver_actor_id: str | None = None
    approved_at: datetime | None
    asset_count: int | None = None


class ScenePatch(BaseModel):
    purpose: str | None = Field(default=None, max_length=2000)
    planned_duration_sec: int | None = Field(default=None, ge=3, le=600)
    narration_text: str | None = Field(default=None, max_length=12000)
    visual_type: str | None = Field(default=None, max_length=64)
    prompt_package_json: dict[str, Any] | None = None
    continuity_tags_json: list[Any] | None = None
    status: str | None = Field(default=None, max_length=32)


class ScenesGenerateBody(BaseModel):
    """Initial scene planning replaces all scenes in the chapter; set true only when replanning intentionally."""

    replace_existing_scenes: bool = False


class SceneImageGenBody(BaseModel):
    generation_tier: Literal["preview", "production"] = "preview"
    image_prompt_override: str | None = Field(default=None, max_length=4000)
    refine_bracket_visual_with_llm: bool = Field(
        default=False,
        description=(
            "When narration contains [bracketed] visual hints, optionally run the text model to merge them "
            "into one precise still prompt. Off by default; requires a configured OpenAI-compatible endpoint."
        ),
    )
    # Studio manual runs: overrides scene package / project.preferred_image_provider for this job only.
    image_provider: str | None = Field(default=None, max_length=64)
    fal_image_model: str | None = Field(
        default=None,
        max_length=256,
        description="When the resolved image provider is fal, call this endpoint_id (e.g. fal-ai/flux/dev).",
    )
    exclude_character_bible: bool = Field(
        default=False,
        description="When true, do not prepend ProjectCharacter consistency text to the image prompt for this job.",
    )


class PromptEnhanceImageBody(BaseModel):
    """Rewrite image retry prompt using previous-scene + character context."""

    current_prompt: str = Field(..., min_length=1, max_length=4000)


class PromptEnhanceVoBody(BaseModel):
    """Rewrite scene VO to match project narration style (or optional override text)."""

    current_script: str = Field(..., min_length=1, max_length=12000)
    narration_style_prompt: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional full style instructions; when omitted, server resolves from project + workspace.",
    )


class PromptExpandVoBody(BaseModel):
    """Expand scene VO into a longer script (~N sentences) with optional user hints."""

    current_script: str = Field(..., min_length=1, max_length=12000)
    target_sentence_count: int = Field(
        default=6,
        ge=1,
        le=40,
        description="Approximate number of complete sentences in the expanded narration.",
    )
    expansion_context: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional notes: what to add, emphasize, or avoid while expanding.",
    )
    narration_style_prompt: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional full style instructions; when omitted, server resolves from project + workspace.",
    )


class SceneVideoGenBody(BaseModel):
    generation_tier: Literal["preview", "production"] = "preview"
    notes: str | None = Field(default=None, max_length=2000)
    video_prompt_override: str | None = Field(
        default=None,
        max_length=3000,
        description="Optional motion/camera prompt for this job; overrides scene prompt_package_json.video_prompt.",
    )
    # Studio manual runs: overrides scene package / project.preferred_video_provider for this job only.
    video_provider: str | None = Field(default=None, max_length=64)
    fal_video_model: str | None = Field(
        default=None,
        max_length=256,
        description="When the resolved video provider is fal, call this endpoint_id.",
    )
    exclude_character_bible: bool = Field(
        default=False,
        description="When true, do not prepend ProjectCharacter consistency text to the video text prompt for this job (fal / Comfy).",
    )


class AssetRejectBody(BaseModel):
    reason: str | None = Field(default=None, max_length=8000)


class SceneAssetSequenceBody(BaseModel):
    """Ordered asset IDs (first = earliest in scene playback order). Other scene assets are renumbered after these."""

    asset_ids: list[UUID] = Field(..., min_length=1)


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scene_id: UUID
    project_id: UUID
    asset_type: str
    status: str
    generation_tier: str
    provider: str | None
    model_name: str | None
    params_json: dict[str, Any] | None
    storage_url: str | None
    preview_url: str | None
    error_message: str | None
    approved_at: datetime | None
    timeline_sequence: int = 0
    created_at: datetime
