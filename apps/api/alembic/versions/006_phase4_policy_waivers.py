"""Phase 4 — per-project critic policy + audited waivers

Revision ID: 006
Revises: 005
Create Date: 2026-03-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("critic_policy_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.add_column(
        "chapters",
        sa.Column("critic_gate_waived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("critic_gate_waiver_actor_id", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("critic_gate_waiver_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("critic_gate_waiver_ticket_url", sa.String(length=2048), nullable=True),
    )

    op.add_column(
        "scenes",
        sa.Column("critic_waived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scenes",
        sa.Column("critic_waiver_actor_id", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "scenes",
        sa.Column("critic_waiver_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scenes", "critic_waiver_reason")
    op.drop_column("scenes", "critic_waiver_actor_id")
    op.drop_column("scenes", "critic_waived_at")

    op.drop_column("chapters", "critic_gate_waiver_ticket_url")
    op.drop_column("chapters", "critic_gate_waiver_reason")
    op.drop_column("chapters", "critic_gate_waiver_actor_id")
    op.drop_column("chapters", "critic_gate_waived_at")

    op.drop_column("projects", "critic_policy_json")
