"""Add projects.frame_aspect_ratio (16:9 vs 9:16).

Revision ID: 028
Revises: 027
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "frame_aspect_ratio",
            sa.String(length=16),
            nullable=False,
            server_default="16:9",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "frame_aspect_ratio")
