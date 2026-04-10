"""User profile: full name, address fields.

Revision ID: 023
Revises: 022
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(length=256), nullable=True))
    op.add_column("users", sa.Column("city", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("state", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("country", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("zip_code", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "zip_code")
    op.drop_column("users", "country")
    op.drop_column("users", "state")
    op.drop_column("users", "city")
    op.drop_column("users", "full_name")
