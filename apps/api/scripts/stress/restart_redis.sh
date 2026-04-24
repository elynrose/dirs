#!/usr/bin/env bash
# Restart Redis in the default director compose stack (disrupts Celery broker).
#
# Usage (repo root):
#   STRESS_COMPOSE_DIR=/path/to/director bash apps/api/scripts/stress/restart_redis.sh
#
# Expect in-flight Celery tasks to fail or retry depending on app logic.

set -euo pipefail

ROOT="${STRESS_COMPOSE_DIR:-$(cd "$(dirname "$0")/../../../.." && pwd)}"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not on PATH" >&2
  exit 1
fi

echo "Restarting redis from $ROOT ..."
docker compose restart redis

echo "Waiting for ping ..."
for i in $(seq 1 30); do
  if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "Redis OK"
    exit 0
  fi
  sleep 1
done

echo "Redis did not become ready in time" >&2
exit 1
