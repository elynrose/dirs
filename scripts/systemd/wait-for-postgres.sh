#!/bin/bash
# Wait until Postgres in Docker accepts connections (avoids API crash on boot).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
for _ in $(seq 1 90); do
  if docker compose exec -T postgres pg_isready -U director -d director >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done
echo "wait-for-postgres: timed out after 90s" >&2
exit 1
