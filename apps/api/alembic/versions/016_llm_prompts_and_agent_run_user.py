"""LLM prompt catalog, per-user overrides, agent run starter user.

Revision ID: 016
Revises: 015
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from director_api.llm_prompt_catalog import LLM_PROMPT_SPECS

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_prompt_definitions",
        sa.Column("prompt_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_content", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("prompt_key"),
    )

    op.create_table(
        "user_llm_prompt_overrides",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_key", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["prompt_key"], ["llm_prompt_definitions.prompt_key"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_llm_prompt_overrides_tenant_id"),
        "user_llm_prompt_overrides",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_llm_prompt_overrides_user_id"),
        "user_llm_prompt_overrides",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_llm_prompt_overrides_prompt_key"),
        "user_llm_prompt_overrides",
        ["prompt_key"],
        unique=False,
    )
    op.create_index(
        "ux_user_llm_prompt_owner",
        "user_llm_prompt_overrides",
        ["tenant_id", "user_id", "prompt_key"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "ux_user_llm_prompt_anon",
        "user_llm_prompt_overrides",
        ["tenant_id", "prompt_key"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )

    now_rows = [
        {
            "prompt_key": s.prompt_key,
            "title": s.title,
            "description": s.description,
            "default_content": s.default_content,
            "sort_order": s.sort_order,
        }
        for s in LLM_PROMPT_SPECS
    ]
    if now_rows:
        op.bulk_insert(
            sa.table(
                "llm_prompt_definitions",
                sa.column("prompt_key", sa.String),
                sa.column("title", sa.String),
                sa.column("description", sa.Text),
                sa.column("default_content", sa.Text),
                sa.column("sort_order", sa.Integer),
            ),
            now_rows,
        )

    op.add_column(
        "agent_runs",
        sa.Column("started_by_user_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_started_by_user_id_users",
        "agent_runs",
        "users",
        ["started_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_agent_runs_started_by_user_id"),
        "agent_runs",
        ["started_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_runs_started_by_user_id"), table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_started_by_user_id_users", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "started_by_user_id")

    op.drop_index("ux_user_llm_prompt_anon", table_name="user_llm_prompt_overrides")
    op.drop_index("ux_user_llm_prompt_owner", table_name="user_llm_prompt_overrides")
    op.drop_index(op.f("ix_user_llm_prompt_overrides_prompt_key"), table_name="user_llm_prompt_overrides")
    op.drop_index(op.f("ix_user_llm_prompt_overrides_user_id"), table_name="user_llm_prompt_overrides")
    op.drop_index(op.f("ix_user_llm_prompt_overrides_tenant_id"), table_name="user_llm_prompt_overrides")
    op.drop_table("user_llm_prompt_overrides")
    op.drop_table("llm_prompt_definitions")
