"""Per-scene playback order for assets (gallery / final-cut sequence).

Revision ID: 013
Revises: 012
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("timeline_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id AS asset_id,
                       ROW_NUMBER() OVER (PARTITION BY scene_id ORDER BY created_at) - 1 AS rn
                FROM assets
            )
            UPDATE assets AS a
            SET timeline_sequence = ranked.rn
            FROM ranked
            WHERE a.id = ranked.asset_id
            """
        )
    )
    op.create_index(
        "ix_assets_scene_timeline_sequence",
        "assets",
        ["scene_id", "timeline_sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_assets_scene_timeline_sequence", table_name="assets")
    op.drop_column("assets", "timeline_sequence")
