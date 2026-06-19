"""Project optional YouTube publish after export.

Revision ID: 042
Revises: 041
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "publish_to_youtube",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "publish_to_youtube")
