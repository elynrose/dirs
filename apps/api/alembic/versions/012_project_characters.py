"""Project characters (visual bible for media consistency)

Revision ID: 012
Revises: 011
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_characters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("role_in_story", sa.Text(), nullable=False),
        sa.Column("visual_description", sa.Text(), nullable=False),
        sa.Column("time_place_scope_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_characters_tenant_id", "project_characters", ["tenant_id"], unique=False)
    op.create_index("ix_project_characters_project_id", "project_characters", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_project_characters_project_id", table_name="project_characters")
    op.drop_index("ix_project_characters_tenant_id", table_name="project_characters")
    op.drop_table("project_characters")
