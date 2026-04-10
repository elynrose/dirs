"""User-defined narration styles (voice briefs for script + scene LLM phases).

Revision ID: 018
Revises: 017
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_narration_styles",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_narration_styles_tenant_id"), "user_narration_styles", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_user_narration_styles_user_id"), "user_narration_styles", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_narration_styles_user_id"), table_name="user_narration_styles")
    op.drop_index(op.f("ix_user_narration_styles_tenant_id"), table_name="user_narration_styles")
    op.drop_table("user_narration_styles")
