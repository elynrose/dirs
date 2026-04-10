"""usage_records: Director credits column.

Revision ID: 024
Revises: 023
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("usage_records", sa.Column("credits", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_records", "credits")
