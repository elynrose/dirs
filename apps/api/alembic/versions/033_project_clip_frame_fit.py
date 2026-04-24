"""Add projects.clip_frame_fit (center_crop vs letterbox for stock/import reframe).

Revision ID: 033
Revises: 032
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "clip_frame_fit",
            sa.String(length=24),
            nullable=False,
            server_default="center_crop",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "clip_frame_fit")
