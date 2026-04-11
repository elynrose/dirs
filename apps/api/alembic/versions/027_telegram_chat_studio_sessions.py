"""Telegram Chat Studio session rows (conversation + brief snapshot).

Revision ID: 027
Revises: 026
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_chat_studio_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("telegram_chat_id", sa.String(length=32), nullable=False),
        sa.Column(
            "messages_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "brief_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "telegram_chat_id", name="uq_telegram_chat_studio_tenant_chat"),
    )
    op.create_index(
        op.f("ix_telegram_chat_studio_sessions_tenant_id"),
        "telegram_chat_studio_sessions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_chat_studio_sessions_telegram_chat_id"),
        "telegram_chat_studio_sessions",
        ["telegram_chat_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_telegram_chat_studio_sessions_telegram_chat_id"), table_name="telegram_chat_studio_sessions")
    op.drop_index(op.f("ix_telegram_chat_studio_sessions_tenant_id"), table_name="telegram_chat_studio_sessions")
    op.drop_table("telegram_chat_studio_sessions")
