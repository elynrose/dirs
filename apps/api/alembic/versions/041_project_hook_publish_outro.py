"""Project opening hook, publish pack, and optional outro flag.

Revision ID: 041
Revises: 040
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("opening_hook_text", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column(
            "publish_pack_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "include_outro_scene",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "include_outro_scene")
    op.drop_column("projects", "publish_pack_json")
    op.drop_column("projects", "opening_hook_text")
