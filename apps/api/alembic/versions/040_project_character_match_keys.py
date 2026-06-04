"""Add match_keys, short_visual_tag, reference_image_url to project_characters.

Revision ID: 040
Revises: 039
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project_characters",
        sa.Column(
            "match_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "project_characters",
        sa.Column("short_visual_tag", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "project_characters",
        sa.Column("reference_image_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_characters", "reference_image_url")
    op.drop_column("project_characters", "short_visual_tag")
    op.drop_column("project_characters", "match_keys")
