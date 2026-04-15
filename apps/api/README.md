# apps/api — Directely API (Phase 1)

**Python 3.11+** FastAPI service. Loads env from **repo root** `.env` or `apps/api/.env` (see `director_api/config.py`).

## Prerequisites

- Docker: `make up` from repo root (Postgres + Redis).
- Python **3.11+** (`uv` recommended).

## Setup

```bash
cd apps/api
uv sync
# or: pip install -e ".[dev]"
```

## Database

From repo root (reads `.env` via `Settings`):

```bash
make migrate
# equivalent: cd apps/api && alembic upgrade head
```

## Run API

```bash
# from repo root
make api
# or: cd apps/api && python -m director_api
```

- OpenAPI: http://127.0.0.1:8000/docs  
- Health: `GET /v1/health`  
- Ready (DB + Redis): `GET /v1/ready`

## Run Celery worker

```bash
make worker
# or: cd apps/api && celery -A director_api.tasks.celery_app worker -Q text,media,compile -l info
```

**Agent runs** (`POST /v1/agent-runs`) and **async jobs** need a **healthy worker** and **Redis**. If the UI sticks on a step (e.g. **outline running**):

1. Confirm `make worker` is running and logs show tasks like `director.run_agent_run` being received (not only `adapter_smoke`).
2. Keep **Docker** Postgres + Redis up (`make up`); a **Broken pipe** / worker crash often means Redis restarted while the worker was idle — restart the worker.
3. With **`OPENAI_API_KEY`** set, outline uses the API for chapter outlines; requests use **`OPENAI_TIMEOUT_SEC`** (default **120** s). If the provider hangs, the call fails and the pipeline falls back to the deterministic outline instead of waiting indefinitely.

## Phase 1 endpoints

| Method | Path | Notes |
| ------ | ---- | ----- |
| POST | `/v1/projects` | Create project; body validated against documentary brief JSON Schema |
| GET | `/v1/projects/{id}` | |
| PATCH | `/v1/projects/{id}` | |
| POST | `/v1/jobs` | `{"type":"adapter_smoke","provider":"openai\|openrouter\|fal"}` — requires `Idempotency-Key` header |
| GET | `/v1/jobs/{id}` | Poll job status |

## Phase 2 endpoints (research & script)

| Method | Path | Notes |
| ------ | ---- | ----- |
| POST | `/v1/projects/{id}/start` | Build & validate director pack (`director-pack/v1`); `workflow_phase` → `director_ready` |
| POST | `/v1/projects/{id}/research/run` | 202 + job — requires `Idempotency-Key`; Celery `research_run` |
| GET | `/v1/projects/{id}/research` | Latest dossier, sources, claims, `script_gate_open` |
| POST | `/v1/projects/{id}/research/approve` | Approve dossier; enables script jobs |
| POST | `/v1/projects/{id}/research/override` | Audit override (`actor_user_id`, `reason`, optional `ticket_url`) |
| POST | `/v1/projects/{id}/script/generate-outline` | 202 — blocked until approve or override |
| POST | `/v1/projects/{id}/script/generate-chapters` | 202 — same gate |
| PATCH | `/v1/chapters/{id}/script` | Human edit `{ "script_text": "..." }` |

## Env vars

| Variable | Purpose |
| -------- | ------- |
| `DATABASE_URL` | `postgresql+psycopg://...` |
| `REDIS_URL` | Celery broker/backend |
| `LOCAL_STORAGE_ROOT` | Filesystem asset root (default `./data/storage` repo-relative from cwd) |
| `OPENAI_API_KEY` | Tier A smoke |
| `OPENAI_TIMEOUT_SEC` | OpenAI HTTP timeout in seconds (default **120**); avoids hung agent-run steps |
| `OPENROUTER_API_KEY` | Tier A smoke |
| `FAL_KEY` | Tier A smoke (image; may incur cost) |
| `TAVILY_API_KEY` | Phase 2 research search provider |
| `CELERY_EAGER` | `1` to run tasks inline (tests only) |
| `AGENT_RUN_FAST` | `1` or `true`: full `POST /v1/agent-runs` skips **LLM scene + chapter critics** and **scene-plan refinement** (still runs director/research/outline/chapters/scene cards). Much faster for local smoke; not for production QA. |
| `OPENAI_AGENTS_PARALLEL` | Default **true**: autonomous agent run fans out **scene** and **chapter** critic LLM calls in parallel via the **[OpenAI Agents SDK](https://github.com/openai/openai-agents-python)** (`asyncio.gather` + structured outputs). Set **`false`** to use the older sequential Chat Completions path. Single-scene/chapter **Celery** jobs still use Chat Completions. |

## Smoke flow

See [`../../docs/ADAPTER_SMOKE.md`](../../docs/ADAPTER_SMOKE.md).
