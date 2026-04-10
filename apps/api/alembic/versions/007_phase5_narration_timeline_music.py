"""Phase 5 — narration tracks, timeline versions, music beds (foundation)

Revision ID: 007
Revises: 006
Create Date: 2026-03-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "narration_tracks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("voice_config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("audio_url", sa.Text(), nullable=True),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_narration_tracks_tenant_id"), "narration_tracks", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_narration_tracks_project_id"), "narration_tracks", ["project_id"], unique=False)
    op.create_index(op.f("ix_narration_tracks_chapter_id"), "narration_tracks", ["chapter_id"], unique=False)

    op.create_table(
        "timeline_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_name", sa.String(length=128), nullable=False),
        sa.Column("timeline_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("render_status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("output_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_timeline_versions_tenant_id"), "timeline_versions", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_timeline_versions_project_id"), "timeline_versions", ["project_id"], unique=False)
    op.alter_column("timeline_versions", "render_status", server_default=None)

    op.create_table(
        "music_beds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("storage_url", sa.Text(), nullable=True),
        sa.Column("license_or_source_ref", sa.Text(), nullable=True),
        sa.Column("mix_config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_music_beds_tenant_id"), "music_beds", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_music_beds_project_id"), "music_beds", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_music_beds_project_id"), table_name="music_beds")
    op.drop_index(op.f("ix_music_beds_tenant_id"), table_name="music_beds")
    op.drop_table("music_beds")

    op.drop_index(op.f("ix_timeline_versions_project_id"), table_name="timeline_versions")
    op.drop_index(op.f("ix_timeline_versions_tenant_id"), table_name="timeline_versions")
    op.drop_table("timeline_versions")

    op.drop_index(op.f("ix_narration_tracks_chapter_id"), table_name="narration_tracks")
    op.drop_index(op.f("ix_narration_tracks_project_id"), table_name="narration_tracks")
    op.drop_index(op.f("ix_narration_tracks_tenant_id"), table_name="narration_tracks")
    op.drop_table("narration_tracks")
