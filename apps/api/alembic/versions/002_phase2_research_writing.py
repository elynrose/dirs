"""phase 2 research and writing tables

Revision ID: 002
Revises: 001
Create Date: 2026-03-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("workflow_phase", sa.String(length=64), nullable=False, server_default="draft"),
    )
    op.add_column("projects", sa.Column("director_output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(
        "projects",
        sa.Column("research_min_sources", sa.Integer(), nullable=False, server_default="3"),
    )
    op.alter_column("projects", "workflow_phase", server_default=None)
    op.alter_column("projects", "research_min_sources", server_default=None)

    op.create_table(
        "research_dossiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("body_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_notes", sa.Text(), nullable=True),
        sa.Column("override_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("override_actor_id", sa.String(length=256), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("override_ticket_url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_research_dossiers_project_id"), "research_dossiers", ["project_id"], unique=False)

    op.create_table(
        "research_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url_or_reference", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=True),
        sa.Column("credibility_score", sa.Float(), nullable=True),
        sa.Column("extracted_facts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("disputed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["dossier_id"], ["research_dossiers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_research_sources_dossier_id"), "research_sources", ["dossier_id"], unique=False)
    op.create_index(op.f("ix_research_sources_project_id"), "research_sources", ["project_id"], unique=False)

    op.create_table(
        "research_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("disputed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("adequately_sourced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_refs_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["dossier_id"], ["research_dossiers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_research_claims_dossier_id"), "research_claims", ["dossier_id"], unique=False)
    op.create_index(op.f("ix_research_claims_project_id"), "research_claims", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_research_claims_project_id"), table_name="research_claims")
    op.drop_index(op.f("ix_research_claims_dossier_id"), table_name="research_claims")
    op.drop_table("research_claims")
    op.drop_index(op.f("ix_research_sources_project_id"), table_name="research_sources")
    op.drop_index(op.f("ix_research_sources_dossier_id"), table_name="research_sources")
    op.drop_table("research_sources")
    op.drop_index(op.f("ix_research_dossiers_project_id"), table_name="research_dossiers")
    op.drop_table("research_dossiers")
    op.drop_column("projects", "research_min_sources")
    op.drop_column("projects", "director_output_json")
    op.drop_column("projects", "workflow_phase")
