"""Singleton platform Stripe settings (admin-editable; env fallback).

Revision ID: 025
Revises: 024
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_stripe_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("stripe_secret_key", sa.Text(), nullable=True),
        sa.Column("stripe_webhook_secret", sa.Text(), nullable=True),
        sa.Column("stripe_publishable_key", sa.Text(), nullable=True),
        sa.Column("billing_success_url", sa.Text(), nullable=True),
        sa.Column("billing_cancel_url", sa.Text(), nullable=True),
        sa.Column("stripe_price_studio_monthly", sa.String(length=128), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
    )
    op.execute(sa.text("INSERT INTO platform_stripe_settings (id) VALUES (1)"))


def downgrade() -> None:
    op.drop_table("platform_stripe_settings")
