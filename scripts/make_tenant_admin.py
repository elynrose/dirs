#!/usr/bin/env python3
"""Grant workspace **admin** role (Studio Admin rail + session-based admin API).

Reads ``DATABASE_URL`` from the repo root ``.env`` (same as the API).

Usage::

  cd apps/api && .venv/bin/python ../../scripts/make_tenant_admin.py user@example.com
  cd apps/api && .venv/bin/python ../../scripts/make_tenant_admin.py user@example.com --tenant-id <uuid>
  cd apps/api && .venv/bin/python ../../scripts/make_tenant_admin.py user@example.com --dry-run

If the user belongs to more than one workspace, you must pass ``--tenant-id``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from sqlalchemy import select

from director_api.config import get_settings
from director_api.db.models import Tenant, TenantMembership, User
from director_api.db.session import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="User email (case-insensitive)")
    parser.add_argument(
        "--tenant-id",
        default="",
        help="Workspace UUID; required when the user has multiple memberships",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions only, do not commit")
    args = parser.parse_args()

    email = args.email.strip().lower()
    if not email or "@" not in email:
        print("error: invalid email", file=sys.stderr)
        return 2

    get_settings()

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"error: no user with email {email!r}", file=sys.stderr)
            return 1

        q = select(TenantMembership).where(TenantMembership.user_id == user.id)
        tid_filter = (args.tenant_id or "").strip()
        if tid_filter:
            q = q.where(TenantMembership.tenant_id == tid_filter)
        rows = list(db.scalars(q).all())

        if not rows:
            suffix = f" for tenant_id={tid_filter!r}" if tid_filter else ""
            print(f"error: no tenant membership for {email!r}{suffix}", file=sys.stderr)
            return 1

        if len(rows) > 1 and not tid_filter:
            print("error: user has multiple workspaces; pass --tenant-id", file=sys.stderr)
            for m in rows:
                t = db.get(Tenant, m.tenant_id)
                name = (t.name if t else "") or ""
                print(f"  {m.tenant_id}  role={m.role!r}  tenant_name={name!r}", file=sys.stderr)
            return 1

        to_commit = 0
        for m in rows:
            prev = (m.role or "").strip().lower()
            if prev == "admin":
                print(f"ok: already admin  tenant_id={m.tenant_id}  email={email}")
                continue
            t = db.get(Tenant, m.tenant_id)
            tname = (t.name if t else "") or ""
            print(f"update: tenant_id={m.tenant_id}  tenant_name={tname!r}  role {m.role!r} -> 'admin'")
            if not args.dry_run:
                m.role = "admin"
                to_commit += 1

        if args.dry_run:
            print("dry-run: no changes committed")
            return 0

        if to_commit:
            db.commit()
            print(f"ok: committed {to_commit} membership update(s) for {email}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
