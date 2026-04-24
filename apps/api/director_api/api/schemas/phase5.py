"""Request/response models for Phase 5 (timeline, music, compile)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TimelineVersionCreate(BaseModel):
    version_name: str = Field(..., min_length=1, max_length=128)
    timeline_json: dict[str, Any]


class TimelineVersionPatch(BaseModel):
    version_name: str | None = Field(None, min_length=1, max_length=128)
    timeline_json: dict[str, Any] | None = None
    render_status: str | None = Field(None, max_length=32)
    output_url: str | None = None


class TimelineVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    version_name: str
    timeline_json: dict[str, Any]
    render_status: str
    output_url: str | None
    created_at: datetime


class MusicBedCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    storage_url: str | None = None
    license_or_source_ref: str | None = None
    mix_config_json: dict[str, Any] | None = None


class MusicBedPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    storage_url: str | None = None
    license_or_source_ref: str | None = None
    mix_config_json: dict[str, Any] | None = None


class MusicBedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    uploaded_by_user_id: int | None = None
    title: str
    storage_url: str | None
    license_or_source_ref: str | None
    mix_config_json: dict[str, Any] | None
    created_at: datetime


class RoughCutBody(BaseModel):
    timeline_version_id: UUID
    allow_unapproved_media: bool = Field(
        default=False,
        description="When true (Hands-off / unattended), use succeeded timeline media even if not approved.",
    )
    require_scene_narration_tracks: bool = Field(
        default=False,
        description="When true, block export if any scene has narration_text but no scene TTS file. "
        "Default false: missing VO is mixed as silence for that clip (final mux).",
    )


class FineCutBody(BaseModel):
    """Same payload as rough-cut: target timeline version (overlays read from ``timeline_json``)."""

    timeline_version_id: UUID
    allow_unapproved_media: bool = Field(
        default=False,
        description="When true (Hands-off / unattended), preflight allows unapproved timeline media.",
    )


class FinalCutBody(RoughCutBody):
    """Final mux: optional burn-in of project ``subtitles.vtt`` when present."""

    burn_subtitles_into_video: bool | None = Field(
        default=None,
        description="When true, burn subtitles into final_cut.mp4. None = use workspace default burn_subtitles_in_final_cut_default.",
    )


class ExportBundleBody(BaseModel):
    timeline_version_id: UUID
    include_subtitles: bool = True
