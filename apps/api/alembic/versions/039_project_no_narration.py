"""Add projects.no_narration (slideshow / music-only exports without TTS).

Revision ID: 039
Revises: 038
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "039"
down_revision: Union[str, None] = "038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "no_narration",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "no_narration")
