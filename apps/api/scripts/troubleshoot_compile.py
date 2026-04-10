"""Enqueue or run Phase 5 rough_cut / final_cut jobs for local troubleshooting.

Run from ``apps/api`` (or any cwd; the script fixes ``sys.path``):

  python scripts/troubleshoot_compile.py rough-cut --project-id <uuid> --timeline-version-id <uuid>
  python scripts/troubleshoot_compile.py final-cut --project-id <uuid> --timeline-version-id <uuid>

  # No Celery worker: execute the worker handler inline (same code path as the compile queue)
  python scripts/troubleshoot_compile.py rough-cut ... --sync

  # Enqueue then wait for terminal status
  python scripts/troubleshoot_compile.py rough-cut ... --poll

  # List timeline versions for a project (discover IDs)
  python scripts/troubleshoot_compile.py list-timelines --project-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

# ``python scripts/troubleshoot_compile.py`` from repo: apps/api
_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from sqlalchemy import select

from director_api.config import get_settings
from director_api.db.models import Job, Project, TimelineVersion
from director_api.db.session import SessionLocal
from director_api.services.runtime_settings import resolve_runtime_settings
from director_api.tasks.job_enqueue import enqueue_job_task
from director_api.tasks.worker_tasks import run_phase5_job


def _parse_uuid(s: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(s.strip())
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid {name} UUID: {s!r}") from e


def _validate_timeline(
    db,
    *,
    project_id: uuid.UUID,
    timeline_version_id: uuid.UUID,
    tenant_id: str,
) -> TimelineVersion:
    tv = db.get(TimelineVersion, timeline_version_id)
    if not tv or str(tv.tenant_id) != str(tenant_id) or tv.project_id != project_id:
        raise SystemExit(
            f"timeline version not found or not in project: {timeline_version_id} / project {project_id}"
        )
    return tv


def _create_compile_job(
    db,
    *,
    job_type: str,
    project_id: uuid.UUID,
    timeline_version_id: uuid.UUID,
    tenant_id: str,
    allow_unapproved: bool,
) -> Job:
    job = Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        type=job_type,
        status="queued",
        payload={
            "timeline_version_id": str(timeline_version_id),
            "project_id": str(project_id),
            "tenant_id": tenant_id,
            "allow_unapproved_media": allow_unapproved,
        },
        project_id=project_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _poll_job(db, job_id: uuid.UUID, *, interval_sec: float, timeout_sec: float) -> Job | None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        db.expire_all()
        job = db.get(Job, job_id)
        if job is None:
            return None
        if job.status in ("succeeded", "failed"):
            return job
        time.sleep(interval_sec)
    return db.get(Job, job_id)


def _cmd_list_timelines(project_id: uuid.UUID) -> int:
    settings = get_settings()
    tenant = settings.default_tenant_id
    with SessionLocal() as db:
        p = db.get(Project, project_id)
        if not p or str(p.tenant_id) != str(tenant):
            print(f"project not found or wrong tenant: {project_id}", file=sys.stderr)
            return 1
        rows = list(
            db.scalars(
                select(TimelineVersion)
                .where(
                    TimelineVersion.project_id == project_id,
                    TimelineVersion.tenant_id == tenant,
                )
                .order_by(TimelineVersion.created_at.desc())
            ).all()
        )
        if not rows:
            print("(no timeline versions)")
            return 0
        for tv in rows:
            print(
                f"{tv.id}\t{tv.version_name!r}\trender_status={tv.render_status}\toutput_url={tv.output_url!r}"
            )
    return 0


def _cmd_compile(
    job_type: str,
    project_id: uuid.UUID,
    timeline_version_id: uuid.UUID,
    *,
    allow_unapproved: bool,
    sync: bool,
    poll_interval: float,
    poll_timeout: float,
) -> int:
    base = get_settings()
    with SessionLocal() as db:
        settings = resolve_runtime_settings(db, base)
        tenant = settings.default_tenant_id
        _validate_timeline(db, project_id=project_id, timeline_version_id=timeline_version_id, tenant_id=tenant)
        job = _create_compile_job(
            db,
            job_type=job_type,
            project_id=project_id,
            timeline_version_id=timeline_version_id,
            tenant_id=tenant,
            allow_unapproved=allow_unapproved,
        )
        jid = job.id
        print(f"job_id={jid}")
        print(f"type={job_type}")
        root = Path(settings.local_storage_root).resolve()
        print(f"expected_rough_cut={root / 'exports' / str(project_id) / str(timeline_version_id) / 'rough_cut.mp4'}")
        print(f"expected_final_cut={root / 'exports' / str(project_id) / str(timeline_version_id) / 'final_cut.mp4'}")

        if sync:
            print("running inline (run_phase5_job.run) …", flush=True)
            run_phase5_job.run(str(jid))
            db.expire_all()
            jdone = db.get(Job, jid)
            if jdone:
                print(f"inline_done status={jdone.status}", flush=True)
                if jdone.error_message:
                    print("error_message:", jdone.error_message[:4000])
                if jdone.status != "succeeded" and poll_interval <= 0:
                    return 1
        else:
            enqueue_job_task(run_phase5_job, jid)
            print("enqueued to Celery (compile queue); ensure worker is running.", flush=True)

        if poll_interval > 0:
            print(f"polling every {poll_interval}s (timeout {poll_timeout}s) …", flush=True)
            jf = _poll_job(db, jid, interval_sec=poll_interval, timeout_sec=poll_timeout)
            if jf is None:
                print("poll: job row missing", file=sys.stderr)
                return 1
            print(f"status={jf.status}")
            if jf.error_message:
                print("error_message:", jf.error_message[:4000])
            if jf.result:
                print("result:", json.dumps(jf.result, indent=2, default=str)[:12000])
            return 0 if jf.status == "succeeded" else 1

    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list-timelines", help="List timeline_version ids for a project")
    p_list.add_argument("--project-id", type=lambda s: _parse_uuid(s, "project_id"), required=True)
    p_list.set_defaults(_fn="list")

    def add_compile_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project-id", type=lambda s: _parse_uuid(s, "project_id"), required=True)
        sp.add_argument(
            "--timeline-version-id",
            type=lambda s: _parse_uuid(s, "timeline_version_id"),
            required=True,
        )
        sp.add_argument(
            "--allow-unapproved",
            action="store_true",
            help="Same as API Hands-off: allow timeline media that is not approved",
        )
        sp.add_argument(
            "--sync",
            action="store_true",
            help="Run compile in-process (no Redis/Celery worker)",
        )
        sp.add_argument(
            "--poll",
            action="store_true",
            help="After enqueue or sync, poll DB until job succeeds/fails or timeout",
        )
        sp.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between polls")
        sp.add_argument("--poll-timeout", type=float, default=3600.0, help="Max seconds to poll")

    p_rough = sub.add_parser("rough-cut", help="Create rough_cut job (video-only compile)")
    add_compile_args(p_rough)
    p_rough.set_defaults(_fn="rough")

    p_final = sub.add_parser("final-cut", help="Create final_cut job (mux narration + music)")
    add_compile_args(p_final)
    p_final.set_defaults(_fn="final")

    ns = p.parse_args(argv)
    if ns._fn == "list":
        return _cmd_list_timelines(ns.project_id)

    poll_interval = ns.poll_interval if ns.poll else 0.0
    job_type = "rough_cut" if ns._fn == "rough" else "final_cut"
    return _cmd_compile(
        job_type,
        ns.project_id,
        ns.timeline_version_id,
        allow_unapproved=ns.allow_unapproved,
        sync=ns.sync,
        poll_interval=poll_interval,
        poll_timeout=ns.poll_timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
