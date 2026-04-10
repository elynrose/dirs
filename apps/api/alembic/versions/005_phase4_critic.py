"""Phase 4 — critic reports and revision issues

Revision ID: 005
Revises: 004
Create Date: 2026-03-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scenes", sa.Column("critic_revision_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("scenes", sa.Column("critic_passed", sa.Boolean(), nullable=True))
    op.alter_column("scenes", "critic_revision_count", server_default=None)

    op.add_column(
        "chapters",
        sa.Column("critic_gate_status", sa.String(length=32), nullable=False, server_default="none"),
    )
    op.alter_column("chapters", "critic_gate_status", server_default=None)

    op.create_table(
        "critic_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("dimensions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("issues_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("recommendations_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("continuity_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("baseline_score", sa.Float(), nullable=True),
        sa.Column("prior_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("meta_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["prior_report_id"], ["critic_reports.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_critic_reports_tenant_id"), "critic_reports", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_critic_reports_project_id"), "critic_reports", ["project_id"], unique=False)
    op.create_index(
        "ix_critic_reports_target",
        "critic_reports",
        ["target_type", "target_id"],
        unique=False,
    )

    op.create_table(
        "revision_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("critic_report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("refs_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("waiver_actor_id", sa.String(length=256), nullable=True),
        sa.Column("waiver_reason", sa.Text(), nullable=True),
        sa.Column("waiver_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["critic_report_id"], ["critic_reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_revision_issues_tenant_id"), "revision_issues", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_revision_issues_project_id"), "revision_issues", ["project_id"], unique=False)
    op.create_index(
        op.f("ix_revision_issues_critic_report_id"),
        "revision_issues",
        ["critic_report_id"],
        unique=False,
    )
    op.alter_column("revision_issues", "status", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_revision_issues_critic_report_id"), table_name="revision_issues")
    op.drop_index(op.f("ix_revision_issues_project_id"), table_name="revision_issues")
    op.drop_index(op.f("ix_revision_issues_tenant_id"), table_name="revision_issues")
    op.drop_table("revision_issues")

    op.drop_index("ix_critic_reports_target", table_name="critic_reports")
    op.drop_index(op.f("ix_critic_reports_project_id"), table_name="critic_reports")
    op.drop_index(op.f("ix_critic_reports_tenant_id"), table_name="critic_reports")
    op.drop_table("critic_reports")

    op.drop_column("chapters", "critic_gate_status")
    op.drop_column("scenes", "critic_passed")
    op.drop_column("scenes", "critic_revision_count")
