# Tenant isolation contract

Canonical tenant sources — do not mix these patterns in new code.

## HTTP requests

- **Source of truth:** `AuthContext.tenant_id` from `auth_context_dep` ([`api/deps.py`](../apps/api/director_api/api/deps.py)).
- **Settings merge:** `settings_dep` calls `resolve_runtime_settings(db, get_settings(), auth.tenant_id, …)` — the returned `Settings.default_tenant_id` is the **active workspace**, not the platform default.
- **Do not** read `get_settings().default_tenant_id` directly in routers when auth is enabled; use `auth.tenant_id` or `settings_dep` after resolution.

## Celery workers

- **Source of truth:** `Job.tenant_id` and `AgentRun.tenant_id` on the ORM row.
- **Runtime settings:** `worker_runtime_for_job(db, job)` / `worker_runtime_for_agent_run(db, run)` in [`worker_helpers.py`](../apps/api/director_api/tasks/worker_helpers.py).
- **Job payloads:** must include `tenant_id` for enqueue paths that workers use for storage or billing; workers should **fail fast** if missing rather than falling back to platform default.

## Storage (`LOCAL_STORAGE_ROOT`)

- Legacy keys are often `assets/<project_id>/…` (project-scoped).
- New writes may use `assets/<tenant_id>/<project_id>/…`; readers should dual-resolve (see `storage/filesystem.py` `resolve_storage_key`).

## Frontend

- Active workspace follows the HttpOnly session cookie; client `tenantId` mirror in [`directorAuthSession.js`](../apps/web/src/lib/directorAuthSession.js) is updated only after successful `POST /v1/auth/session-tenant`.
