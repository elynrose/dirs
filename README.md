# Directely

Production specification and phased build plan for **Directely**, an **AI documentary studio** (multi-agent research → script → scenes → media → critique → FFmpeg compile). The **backend and workers are Python-based** (FastAPI); the **web UI** is TypeScript (Next.js) per [`project.md`](project.md) §4.8 and §11.

## Start here

| Document | Purpose |
| -------- | ------- |
| [`INSTALLATION.md`](INSTALLATION.md) | **Linux server:** Docker, Python venv, migrations, systemd |
| [`docs/GITHUB.md`](docs/GITHUB.md) | **GitHub:** remote, push, clone, secrets |
| [`project.md`](project.md) | Full product and technical specification |
| [`phases/README.md`](phases/README.md) | Trackable phase checklists (P1–P6) |
| [`docs/README.md`](docs/README.md) | Operational detail: API outline, errors, webhooks, runbooks, ADRs |

## Local dependencies (Phase 1+)

The app is **local-first**: assets live under **`LOCAL_STORAGE_ROOT`** (filesystem) by default; see [`docs/local-first-storage.md`](docs/local-first-storage.md).

```bash
cp .env.example .env
make up
```

Services: PostgreSQL, Redis, MinIO (optional **localhost** S3-compatible API). You can run **SQLite-only** later without Compose once the API supports it—see spec §4.7.

## Repository layout (target monorepo)

```
apps/web/          # Next.js (Phase 1 scaffold: README only until implemented)
apps/api/          # Python FastAPI (canonical backend)
services/          # Split workers (see services/README.md)
packages/schemas/  # JSON Schema + golden fixtures
docs/              # API, errors, runbooks, threat model
adr/               # Architecture decision records
```

## MVP status (engineering)

Phases **P1–P6** are marked **done** in [`phases/`](phases/) for a **local-first MVP**: FastAPI + Celery + Postgres + Redis, scene images via Fal, critic gates + waivers, FFmpeg **rough → final (mux)** → **export** bundle, **WebVTT** subtitles, **audit** trail, **job caps**, **rate limits**, **`/v1/metrics`**, and **stale-job reaping** (`director.reap_stale_jobs` — run **Celery beat** alongside the worker). **Not** included: managed IdP, Prometheus/Grafana, load-test artifacts, real video encoder, TTS audio files, and full §10.6 SLO measurement (see phase-06 notes).

```bash
# API (from apps/api)
pip install -e .              # pulls packages/ffmpeg-pipelines
alembic upgrade head
uvicorn director_api.main:app --reload --host 0.0.0.0 --port 8000

# Worker + beat (separate terminals)
celery -A director_api.tasks.celery_app worker -Q text,media,compile -l info
celery -A director_api.tasks.celery_app beat -l info
```

Install **FFmpeg** on the worker host for compile jobs. Web studio: `apps/web` (`npm run dev`, proxy `/v1` to the API).

## Scripts

| Command | Description |
| ------- | ----------- |
| `make up` | Start Docker Compose stack |
| `make down` | Stop stack |
| `make ps` | Show container status |
| `make migrate` | Run Alembic (`apps/api`) — needs Python 3.11+ on PATH |
| `make api` | Start FastAPI (`python -m director_api` from `apps/api`) |
| `make worker` | Start Celery worker |
| `./scripts/telegram-set-webhook.sh https://YOUR_PUBLIC_HOST` | Register Telegram `setWebhook` (needs `TELEGRAM_BOT_TOKEN` + `TELEGRAM_WEBHOOK_SECRET`; see [INSTALLATION.md](INSTALLATION.md) §9) |
| `Launch.cmd` / `Launch.ps1` (repo root) | **Windows:** Docker Compose, migrate, API + Celery worker + beat + Vite + browser |
| `./Launch.sh` (repo root) | **macOS / Linux:** same stack in background + logs under `.run/` + opens browser |

**Phase 1:** after `make up`, run `make migrate`, then `make api` and `make worker` in separate terminals. See [`apps/api/README.md`](apps/api/README.md) and [`docs/ADAPTER_SMOKE.md`](docs/ADAPTER_SMOKE.md).

## Distribution (desktop installer)

For a **production Windows installer** (bundled web build + API sources + Docker Compose template for Postgres/Redis/MinIO), from the repo root on Windows with Node 18+ and Docker available:

```powershell
.\scripts\build-exe.ps1
```

Output in `apps/electron/release/`: **`Directely Setup <version>.exe`** (NSIS installer) and **`Directely-<version>-win-x64.zip`** (portable extract-and-run bundle). Version comes from `apps/electron/package.json` → `version`. The app expects **Docker Desktop** at runtime and **FFmpeg** on `PATH` (or `FFMPEG_BIN` in `.env`). macOS DMG/ZIP targets are configured in `apps/electron/package.json` under `build.mac` — run `npm run dist` from `apps/electron` after `npm run build:web`.

Strip secrets before sharing any workspace: use `.env.example` as the template only; never ship a real `.env` or Firebase service-account JSON.

**Legal / compliance:** proprietary terms in [`LICENSE`](LICENSE), OSS and runtime notes in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), and a pre-ship checklist in [`docs/COMMERCIAL_RELEASE.md`](docs/COMMERCIAL_RELEASE.md). Bump aligned versions with `.\scripts\sync-release-version.ps1 -Version x.y.z` (needs Python on `PATH`; calls `scripts/sync_release_version.py`).
