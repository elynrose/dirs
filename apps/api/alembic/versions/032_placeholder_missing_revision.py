"""Revision 032 — replace this file with the canonical migration from your server.

Your database already has ``alembic_version = '032'``. The real ``032_*.py``
migration lives on the deployed host (web server / app server), not in this
checkout until you copy it in.

**What to do**

1. On the server, locate the file under the API tree, e.g.
   ``apps/api/alembic/versions/032_<something>.py`` (exact name may differ).
2. Copy its contents into this repo. Either:
   - overwrite *this* file with the server file body (keep any filename; only
     ``revision`` / ``down_revision`` inside the file matter), or
   - add the server file alongside and **delete** this placeholder so only
     one file defines ``revision = "032"``.
3. Run ``alembic current`` and ``alembic upgrade head`` from ``apps/api`` to
   confirm the graph resolves.

**Important**

- The server migration must use ``revision = "032"`` (or edit your DB’s
  ``alembic_version`` to match whatever revision id that file uses — avoid
  unless you know what you are doing).
- If the server’s ``down_revision`` is not ``"031"``, the linear chain is
  different; fix ``down_revision`` / ordering to match reality, or ask whoever
  authored 032 on the server.

Until you paste the real migration here, this file remains a **no-op** so
local Alembic can at least load the revision graph.
"""

from typing import Sequence, Union

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
