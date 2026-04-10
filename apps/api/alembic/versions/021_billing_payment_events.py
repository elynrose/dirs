"""Stripe payment event audit log for admin.

Revision ID: 021
Revises: 020
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "billing_payment_events",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("stripe_object_id", sa.String(length=128), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("livemode", sa.Boolean(), nullable=True),
        sa.Column("payload_summary_json", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_billing_payment_events_stripe_event_id", "billing_payment_events", ["stripe_event_id"], unique=True)
    op.create_index("ix_billing_payment_events_event_type", "billing_payment_events", ["event_type"])
    op.create_index("ix_billing_payment_events_tenant_id", "billing_payment_events", ["tenant_id"])
    op.create_index("ix_billing_payment_events_stripe_object_id", "billing_payment_events", ["stripe_object_id"])


def downgrade() -> None:
    op.drop_index("ix_billing_payment_events_stripe_object_id", table_name="billing_payment_events")
    op.drop_index("ix_billing_payment_events_tenant_id", table_name="billing_payment_events")
    op.drop_index("ix_billing_payment_events_event_type", table_name="billing_payment_events")
    op.drop_index("ix_billing_payment_events_stripe_event_id", table_name="billing_payment_events")
    op.drop_table("billing_payment_events")
