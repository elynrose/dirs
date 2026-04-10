"""Subscription plans + tenant billing (Stripe-ready, admin-configurable entitlements).

Revision ID: 020
Revises: 019
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscription_plans",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stripe_price_id", sa.String(length=128), nullable=True),
        sa.Column("stripe_product_id", sa.String(length=128), nullable=True),
        sa.Column("billing_interval", sa.String(length=32), nullable=False, server_default="month"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entitlements_json", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscription_plans_slug", "subscription_plans", ["slug"], unique=True)
    op.create_index("ix_subscription_plans_stripe_price_id", "subscription_plans", ["stripe_price_id"])

    op.create_table(
        "tenant_billing",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=128), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=128), nullable=True),
        sa.Column("plan_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entitlements_override_json", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["subscription_plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_index("ix_tenant_billing_stripe_customer_id", "tenant_billing", ["stripe_customer_id"])
    op.create_index("ix_tenant_billing_stripe_subscription_id", "tenant_billing", ["stripe_subscription_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_billing_stripe_subscription_id", table_name="tenant_billing")
    op.drop_index("ix_tenant_billing_stripe_customer_id", table_name="tenant_billing")
    op.drop_table("tenant_billing")
    op.drop_index("ix_subscription_plans_stripe_price_id", table_name="subscription_plans")
    op.drop_index("ix_subscription_plans_slug", table_name="subscription_plans")
    op.drop_table("subscription_plans")
