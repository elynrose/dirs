#!/usr/bin/env python3
"""Backfill ``usage_records.cost_estimate`` from ``credits`` for legacy non-token rows.

Usage (from ``apps/api`` with venv active)::

    python scripts/backfill_usage_cost_estimate.py [--tenant-id TID] [--dry-run]

Rows updated: ``unit_type != 'tokens'``, positive ``credits``, and
``cost_estimate`` is NULL or zero.
"""

from __future__ import annotations

import argparse

from sqlalchemy import and_, or_, select

from director_api.config import get_settings
from director_api.db.models import UsageRecord
from director_api.db.session import SessionLocal
from director_api.services.usage_credits import CREDITS_PER_USD


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=None, help="Only rows for this tenant_id")
    parser.add_argument("--dry-run", action="store_true", help="Count matches without committing")
    args = parser.parse_args()

    get_settings()

    conds = [
        UsageRecord.unit_type != "tokens",
        UsageRecord.credits > 0,
        or_(UsageRecord.cost_estimate.is_(None), UsageRecord.cost_estimate == 0.0),
    ]
    if args.tenant_id:
        conds.append(UsageRecord.tenant_id == str(args.tenant_id).strip())

    stmt = select(UsageRecord).where(and_(*conds))
    updated = 0
    with SessionLocal() as db:
        for row in db.scalars(stmt).yield_per(400):
            row.cost_estimate = float(row.credits or 0.0) / CREDITS_PER_USD
            updated += 1
        if args.dry_run:
            db.rollback()
            print(f"dry_run: would update {updated} usage_records")
        else:
            db.commit()
            print(f"updated {updated} usage_records")


if __name__ == "__main__":
    main()
