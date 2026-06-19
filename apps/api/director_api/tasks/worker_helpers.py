"""Shared worker utilities — runtime settings, job synthesis, stop signals, usage records.

TenantScoped: use Job.tenant_id / AgentRun.tenant_id via worker_runtime_for_* (see docs/tenant-contract.md).
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable

from sqlalchemy import func, select

from director_api.config import Settings, get_settings
from director_api.db.models import AgentRun, Asset, Job, Scene, UsageRecord
from director_api.db.session import SessionLocal
from director_api.services.runtime_settings import resolve_runtime_settings


def worker_runtime_for_job(db, job: Job) -> Settings:
    return resolve_runtime_settings(db, get_settings(), job.tenant_id, user_id=None)


def worker_runtime_for_agent_run(db, run: AgentRun) -> Settings:
    return resolve_runtime_settings(
        db, get_settings(), run.tenant_id, user_id=run.started_by_user_id
    )


# Back-compat aliases used across worker_tasks / phase3_impl / agent_impl
_worker_runtime_for_job = worker_runtime_for_job
_worker_runtime_for_agent_run = worker_runtime_for_agent_run


def payload_stop_requested(payload: Any) -> bool:
    """True when a job payload carries ``stop_requested=True`` (set by /cancel)."""
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("stop_requested"))


_payload_stop_requested = payload_stop_requested


def worker_tenant_id(job: Job, payload: dict | None = None) -> str:
    """Canonical tenant for worker handlers — Job.tenant_id with optional payload stamp."""
    pl = payload if payload is not None else (job.payload if isinstance(job.payload, dict) else {})
    tid = str(pl.get("tenant_id") or job.tenant_id or "").strip()
    if not tid:
        raise ValueError("missing tenant_id on job")
    return tid


_worker_tenant_id = worker_tenant_id


def make_job_stop_signal(
    *,
    agent_run_uuid: uuid.UUID | None,
    job_uuid: uuid.UUID | None,
    project_uuid: uuid.UUID | None = None,
    min_interval_sec: float = 2.0,
) -> Callable[[], bool]:
    """Return a composite ``() -> bool`` stop callback for long provider polls."""
    if agent_run_uuid is None and job_uuid is None and project_uuid is None:

        def _noop() -> bool:
            return False

        return _noop

    state = {"last_check": 0.0, "last_result": False}

    def _check() -> bool:
        now = time.monotonic()
        if state["last_result"]:
            return True
        if (now - state["last_check"]) < min_interval_sec:
            return state["last_result"]
        state["last_check"] = now
        try:
            with SessionLocal() as db_local:
                if agent_run_uuid is not None:
                    r = db_local.get(AgentRun, agent_run_uuid)
                    if r is None:
                        state["last_result"] = True
                        return True
                    ctrl = r.pipeline_control_json if isinstance(r.pipeline_control_json, dict) else {}
                    if bool(ctrl.get("stop_requested")) or r.status in ("cancelled", "failed"):
                        state["last_result"] = True
                        return True

                if job_uuid is not None:
                    j = db_local.get(Job, job_uuid)
                    if j is not None:
                        if j.status in ("cancelled", "failed"):
                            state["last_result"] = True
                            return True
                        if payload_stop_requested(j.payload):
                            state["last_result"] = True
                            return True

                if project_uuid is not None:
                    pending = db_local.execute(
                        select(AgentRun.pipeline_control_json, AgentRun.status).where(
                            AgentRun.project_id == project_uuid,
                            AgentRun.status.in_(("running", "paused", "queued")),
                        )
                    ).all()
                    for ctrl_json, _status in pending:
                        if not isinstance(ctrl_json, dict):
                            continue
                        if bool(ctrl_json.get("stop_requested")):
                            state["last_result"] = True
                            return True

                return False
        except Exception:  # noqa: BLE001
            return False

    return _check


_make_job_stop_signal = make_job_stop_signal


def make_agent_run_stop_signal(
    agent_run_uuid: uuid.UUID | None, *, min_interval_sec: float = 2.0
) -> Callable[[], bool]:
    """Back-compat wrapper around :func:`make_job_stop_signal`."""
    return make_job_stop_signal(
        agent_run_uuid=agent_run_uuid,
        job_uuid=None,
        project_uuid=None,
        min_interval_sec=min_interval_sec,
    )


_make_agent_run_stop_signal = make_agent_run_stop_signal


def record_usage(
    db,
    *,
    tenant_id: str,
    project_id: uuid.UUID | None,
    scene_id: uuid.UUID | None,
    asset_id: uuid.UUID | None,
    provider: str,
    service_type: str,
    meta: dict[str, Any] | None = None,
    units: float = 1.0,
    unit_type: str = "request",
    cost_estimate: float = 0.0,
) -> None:
    from director_api.services.usage_credits import CREDITS_PER_USD, compute_request_credits

    m = dict(meta or {})
    cr = compute_request_credits(
        provider=provider,
        service_type=service_type,
        unit_type=unit_type,
        units=units,
        meta=m,
    )
    ce = float(cost_estimate or 0.0)
    ut_low = str(unit_type or "").strip().lower()
    if ce <= 0.0 and cr > 0.0 and ut_low != "tokens":
        ce = float(cr) / CREDITS_PER_USD
    db.add(
        UsageRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            scene_id=scene_id,
            asset_id=asset_id,
            provider=provider,
            service_type=service_type,
            units=float(units),
            unit_type=unit_type,
            cost_estimate=ce,
            credits=cr,
            meta_json=m,
        )
    )


_record_usage = record_usage


def next_timeline_sequence_for_scene(db, scene_id: uuid.UUID) -> int:
    """Allocate the next gallery slot for ``scene_id`` (serialized per scene row)."""
    sc = db.scalar(select(Scene).where(Scene.id == scene_id).with_for_update())
    if sc is None:
        raise ValueError("scene not found")
    max_seq = db.scalar(select(func.max(Asset.timeline_sequence)).where(Asset.scene_id == scene_id))
    asset_count = db.scalar(select(func.count()).select_from(Asset).where(Asset.scene_id == scene_id))
    next_from_max = 0 if max_seq is None else int(max_seq) + 1
    next_from_count = int(asset_count or 0)
    return max(next_from_max, next_from_count)


_next_timeline_sequence_for_scene = next_timeline_sequence_for_scene


def synthetic_job(
    *,
    tenant_id: str,
    project_id: uuid.UUID,
    jtype: str,
    payload: dict[str, Any],
) -> Job:
    return Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        type=jtype,
        status="queued",
        payload=payload,
        project_id=project_id,
    )


_synthetic_job = synthetic_job


@contextmanager
def asset_running_guard(
    db: Any,
    asset: Asset,
    *,
    service_type: str,
    tenant_id: str,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
):
    """On provider exception, flip asset to failed before re-raising."""
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        try:
            asset.status = "failed"
            asset.error_message = f"worker_failure: {type(exc).__name__}: {exc}"[:8000]
            db.flush()
            record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project_id,
                scene_id=scene_id,
                asset_id=asset.id,
                provider=str(getattr(asset, "provider", None) or "unknown"),
                service_type=service_type,
                meta={
                    "ok": False,
                    "error": str(exc)[:500],
                    "tier": str(getattr(asset, "generation_tier", None) or "preview"),
                    "crash": True,
                },
            )
            db.flush()
        except Exception:  # noqa: BLE001
            pass
        raise


_asset_running_guard = asset_running_guard
