# Director (Electron)

Desktop shell for the studio UI: starts **Docker Compose** (Postgres, Redis, MinIO), bootstraps a **Python venv** under the app user-data directory, runs **migrations**, then **API + Celery worker + Celery beat**, and serves the built web app on a local port with a **`/v1` proxy** to the API.

## Prerequisites

- **Docker Desktop** (or Docker Engine) with `docker compose` on `PATH`
- **Python 3.11+** on `PATH`. On macOS, Apple’s `python3` is often **3.9** — install e.g. `brew install python@3.12` so `python3.12` exists. Windows: use the **py** launcher (`py -3.12`) or Python 3.11+ from python.org.
- Network on **first run** (pip installs the API package)

## Develop (from repo)

```bash
cd apps/web && npm ci && npm run build
cd ../electron && npm ci && npm start
```

## Packaged build (macOS / Windows / Linux)

```bash
cd apps/electron && npm ci && npm run dist
```

Artifacts appear under `apps/electron/release/`.

## Config

On first launch, `.env.example` from the repo is copied to the app user-data folder as `.env` if missing. Edit that file for API keys and overrides; **restart** the app to apply.

Asset storage defaults to `<userData>/storage` (`LOCAL_STORAGE_ROOT`).

## Pointing the UI at a hosted API (SaaS)

The packaged app serves the same Vite build as `apps/web`. To use a remote Director API instead of the bundled local stack, build the web app with `VITE_API_BASE_URL` set to your API origin (no trailing slash), e.g. `https://api.example.com`, then rebuild Electron so `dist` embeds that base URL. Users sign in via the Studio login screen when `DIRECTOR_AUTH_ENABLED=true` on the server. For OAuth-style flows later, prefer opening the system browser and deep-linking back into the app with a short-lived token.

## Quit behavior

Closing the app runs **`docker compose down`** for the bundled compose file and stops API/worker/beat processes.
