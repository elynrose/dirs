"""One-off: print rough cut / agent run status from DB."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

_API = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_API))

from sqlalchemy import desc, select

from director_api.db.models import AgentRun, Job, Project, TimelineVersion
from director_api.db.session import SessionLocal

ROOT = Path(r"d:\Directely\data\storage")
PROJECT_ID = uuid.UUID("f660b85a-ace4-49a1-9328-997555546b1e")


def main() -> None:
    with SessionLocal() as db:
        proj = db.get(Project, PROJECT_ID)
        print(f"Project: {proj.title if proj else '?'} ({PROJECT_ID})")

        runs = db.scalars(
            select(AgentRun)
            .where(AgentRun.project_id == PROJECT_ID)
            .order_by(desc(AgentRun.updated_at))
            .limit(6)
        ).all()
        print("\n=== Agent runs ===")
        for r in runs:
            print(f"  {r.updated_at}  status={r.status:10}  step={r.current_step or '-'}")
            print(f"    id={r.id}")
            if r.error_message:
                print(f"    error: {r.error_message[:400]}")
            ev = r.steps_json if isinstance(r.steps_json, list) else []
            for e in ev[-6:]:
                if isinstance(e, dict):
                    print(f"    event: {e.get('stage')} / {e.get('status')}")

        tvs = db.scalars(
            select(TimelineVersion)
            .where(TimelineVersion.project_id == PROJECT_ID)
            .order_by(desc(TimelineVersion.created_at))
            .limit(6)
        ).all()
        print("\n=== Timeline versions ===")
        for tv in tvs:
            rough = ROOT / "exports" / str(PROJECT_ID) / str(tv.id) / "rough_cut.mp4"
            final = ROOT / "exports" / str(PROJECT_ID) / str(tv.id) / "final_cut.mp4"
            rb = rough.stat().st_size if rough.is_file() else 0
            fb = final.stat().st_size if final.is_file() else 0
            print(f"  tv={tv.id}")
            print(f"    render_status={tv.render_status}  created={tv.created_at}")
            print(f"    rough_cut.mp4: {rough.is_file()} ({rb:,} bytes)")
            print(f"    final_cut.mp4: {final.is_file()} ({fb:,} bytes)")

        jobs = db.scalars(
            select(Job).where(Job.type.in_(["rough_cut", "final_cut"])).order_by(desc(Job.created_at)).limit(8)
        ).all()
        print("\n=== Compile jobs (rough/final) ===")
        if not jobs:
            print("  (none in DB — rough_cut may run inline via CELERY_EAGER in agent run)")
        for j in jobs:
            print(f"  {j.created_at}  {j.type}  {j.status}  id={j.id}")
            if j.error_message:
                print(f"    {j.error_message[:300]}")


if __name__ == "__main__":
    main()
