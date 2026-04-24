# director-local

This folder is a **snapshot** of the Directely `director` repo, configured for **local-only** development: loopback API, auth off, placeholder images, `local_ffmpeg` video stubs, and **Celery eager** mode (no separate worker process required).

Your original `director/.env` was **not** copied (secrets). Use the included **`.env`** here as a starting point.

**Trimmed for download:** `data/storage` blobs, `apps/electron/release` build outputs, `.venv` / `node_modules`, and `.run` caches were omitted or cleared. Re-run `npm install` / `npm run pack` under `apps/electron` if you need desktop builds.

## Prerequisites

- Docker (for Postgres + Redis from `docker-compose.yml`)
- Python **3.11+**
- Node **18+** (for Studio web)

## 1. Infra

From **this directory** (`director-local`):

```bash
docker compose -p directorlocal up -d
```

Use a **project name** (`-p directorlocal`) if you already run another `director` checkout with the same compose file, so container and volume names do not clash. Stop the other stack if port `5433` or `6379` is already in use.

Postgres: `localhost:5433` · Redis: `localhost:6379`

## 2. API (Python venv)

```bash
cd apps/api
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd ../..
make migrate
make api
```

`make api` loads the repo-root `.env` (see `Makefile`).

## 3. Studio (Vite)

Default `apps/web/.env.development` uses the Vite proxy to the API — keep **`VITE_API_BASE_URL` unset** so the browser talks to same-origin `/v1` and avoids CORS.

```bash
cd apps/web
npm install
npm run dev
```

Open the URL Vite prints (usually `http://localhost:5173`).

## 4. Text / speech keys

Agent runs need a **text LLM**. Add to `.env`, for example:

- `OPENAI_API_KEY=...` (with `ACTIVE_TEXT_PROVIDER=openai`), or  
- Local **LM Studio**: `LM_STUDIO_API_BASE_URL=http://127.0.0.1:1234` and `OPENAI_COMPATIBLE_TEXT_SOURCE=lm_studio` (see `.env.example` in repo root for full list).

Narration TTS still follows `ACTIVE_SPEECH_PROVIDER` (default `openai`); add a key or switch speech provider after reading `INSTALLATION.md` / Studio settings.

## 5. Optional: real media

- **ComfyUI**: set `COMFYUI_BASE_URL`, `COMFYUI_WORKFLOW_JSON_PATH`, and (for WAN) `COMFYUI_VIDEO_WORKFLOW_JSON_PATH` to API-export JSON paths; set `ACTIVE_IMAGE_PROVIDER` / `ACTIVE_VIDEO_PROVIDER` to `comfyui` / `comfyui_wan` when ready.
- **Fal / others**: see root `.env.example`.

## 6. Celery worker (optional)

With `CELERY_EAGER=true`, you do **not** need `make worker`. For production-like local testing, set `CELERY_EAGER=false` in `.env` and run:

```bash
make worker
```

## Download size

This tree includes **`.git`** (~large). For a smaller zip you may delete `.git` here and re-clone upstream later, or run `git clone` fresh and copy only `.env` + `LOCAL-SETUP.md` patterns.

## Syncing from upstream

This is a copy, not a fork remote. To refresh code:

```bash
rsync -a --delete --exclude '.env' --exclude 'apps/api/.venv' --exclude 'node_modules' \
  /path/to/upstream/director/ ./director-local/
```

(or use `git pull` inside if you replaced remotes on this tree).
