"""Project ideas (saved concepts) and scheduled pipeline runs.

Revision ID: 031
Revises: 030
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_ideas",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_topic", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_ideas_tenant_id", "project_ideas", ["tenant_id"], unique=False)

    op.create_table(
        "idea_scheduled_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["idea_id"], ["project_ideas.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_idea_scheduled_runs_tenant_id", "idea_scheduled_runs", ["tenant_id"], unique=False)
    op.create_index("ix_idea_scheduled_runs_idea_id", "idea_scheduled_runs", ["idea_id"], unique=False)
    op.create_index("ix_idea_scheduled_runs_scheduled_at", "idea_scheduled_runs", ["scheduled_at"], unique=False)
    op.create_index("ix_idea_scheduled_runs_status", "idea_scheduled_runs", ["status"], unique=False)
    op.create_index("ix_idea_scheduled_runs_agent_run_id", "idea_scheduled_runs", ["agent_run_id"], unique=False)


def downgrade() -> None:
    op.drop_table("idea_scheduled_runs")
    op.drop_table("project_ideas")
