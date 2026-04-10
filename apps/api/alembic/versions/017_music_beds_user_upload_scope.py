"""Music beds: user uploads visible across projects (tenant-scoped).

Revision ID: 017
Revises: 016
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "music_beds",
        sa.Column("uploaded_by_user_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_music_beds_uploaded_by_user_id"),
        "music_beds",
        ["uploaded_by_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "music_beds_uploaded_by_user_id_fkey",
        "music_beds",
        "users",
        ["uploaded_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("music_beds_project_id_fkey", "music_beds", type_="foreignkey")
    op.alter_column(
        "music_beds",
        "project_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )
    op.create_foreign_key(
        "music_beds_project_id_fkey",
        "music_beds",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("music_beds_project_id_fkey", "music_beds", type_="foreignkey")
    op.alter_column(
        "music_beds",
        "project_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "music_beds_project_id_fkey",
        "music_beds",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("music_beds_uploaded_by_user_id_fkey", "music_beds", type_="foreignkey")
    op.drop_index(op.f("ix_music_beds_uploaded_by_user_id"), table_name="music_beds")
    op.drop_column("music_beds", "uploaded_by_user_id")
