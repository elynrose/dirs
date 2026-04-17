#!/usr/bin/env bash
# Restart Postgres in the default director compose stack (kills DB connections).
#
# Usage (repo root):
#   STRESS_COMPOSE_DIR=/path/to/director bash apps/api/scripts/stress/restart_postgres.sh
#
# Expect API 503/500 until Postgres is healthy again; workers may need restart.

set -euo pipefail

ROOT="${STRESS_COMPOSE_DIR:-$(cd "$(dirname "$0")/../../../.." && pwd)}"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not on PATH" >&2
  exit 1
fi

echo "Restarting postgres from $ROOT ..."
docker compose restart postgres

echo "Waiting for pg_isready ..."
for i in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-director}" -d "${POSTGRES_DB:-director}" >/dev/null 2>&1; then
    echo "Postgres OK"
    exit 0
  fi
  sleep 1
done

echo "Postgres did not become ready in time" >&2
exit 1
