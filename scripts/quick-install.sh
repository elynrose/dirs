#!/usr/bin/env bash
# One-shot local stack: Postgres + Redis, .env with default admin API key, Alembic migrations.
# Usage: from repo root — bash scripts/quick-install.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi
# Default platform admin key for X-Director-Admin-Key (change before any untrusted network exposure).
if ! grep -qE '^[[:space:]]*DIRECTOR_ADMIN_API_KEY=' .env; then
  echo 'DIRECTOR_ADMIN_API_KEY=director-quick-install-admin-change-me' >> .env
  echo "Appended DIRECTOR_ADMIN_API_KEY (change this in production)."
fi

docker compose up -d postgres redis
echo "Waiting for Postgres to accept connections…"
for i in {1..40}; do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-director}" -d "${POSTGRES_DB:-director}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if command -v uv >/dev/null 2>&1; then
  (cd "$ROOT/apps/api" && uv sync --quiet && uv run alembic upgrade head)
elif command -v poetry >/dev/null 2>&1; then
  (cd "$ROOT/apps/api" && poetry install --quiet && poetry run alembic upgrade head)
else
  (cd "$ROOT/apps/api" && python -m pip install -e ".[dev]" -q && python -m alembic upgrade head)
fi

echo ""
echo "Quick install done."
echo "  • Postgres: localhost:5433 (see POSTGRES_* in .env)"
echo "  • Redis: localhost:6379"
echo "  • Admin API: set header X-Director-Admin-Key to the value of DIRECTOR_ADMIN_API_KEY in .env"
echo "  • Start API: cd apps/api && uv run uvicorn director_api.main:app --host 0.0.0.0 --port 8000"
echo "  • Start worker: cd apps/api && uv run celery -A director_api.tasks.celery_app worker -Q compile,default -l info"
