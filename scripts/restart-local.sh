#!/usr/bin/env bash
# Stop and restart local Directely API + Celery worker + Celery beat. See --help.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTENV="$ROOT/.env"
API_DIR="$ROOT/apps/api"
VENV="$API_DIR/.venv"
VENV_PY="$VENV/bin/python"
VENV_CELERY="$VENV/bin/celery"
RUN_DIR="$ROOT/.run"

load_env() {
  set -a
  # shellcheck source=/dev/null
  [[ -f "$DOTENV" ]] && . "$DOTENV"
  set +a
}

stop_all() {
  load_env
  local port="${API_PORT:-8000}"
  local round=0
  # Repeat until no LISTEN remains (uvicorn --reload can leave multiple processes).
  while [[ $round -lt 12 ]]; do
    local pids
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -z "$pids" ]]; then
      break
    fi
    echo "restart-local: stopping API on port $port (PIDs: $pids)"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.4
    round=$((round + 1))
  done
  if pgrep -f "director_api.tasks.celery_app" >/dev/null 2>&1; then
    echo "restart-local: stopping Celery (director_api.tasks.celery_app)"
    pkill -9 -f "director_api.tasks.celery_app" 2>/dev/null || true
  fi
  pkill -9 -f "python -m director_api" 2>/dev/null || true
  sleep 1
}

start_background() {
  [[ -x "$VENV_PY" ]] || {
    echo "restart-local: missing $VENV_PY — run: cd apps/api && uv sync (or pip install -e .)"
    exit 1
  }
  [[ -x "$VENV_CELERY" ]] || {
    echo "restart-local: missing $VENV_CELERY"
    exit 1
  }

  mkdir -p "$RUN_DIR"

  cd "$API_DIR"
  set -a
  # shellcheck source=/dev/null
  [[ -f "$DOTENV" ]] && . "$DOTENV"
  set +a

  nohup "$VENV_CELERY" -A director_api.tasks.celery_app worker -Q text,media,compile -l info \
    >>"$RUN_DIR/director-worker.log" 2>&1 &
  echo $! >"$RUN_DIR/director-worker.pid"
  echo "restart-local: Celery worker PID $(cat "$RUN_DIR/director-worker.pid") → $RUN_DIR/director-worker.log"

  nohup "$VENV_CELERY" -A director_api.tasks.celery_app beat -l info \
    >>"$RUN_DIR/director-beat.log" 2>&1 &
  echo $! >"$RUN_DIR/director-beat.pid"
  echo "restart-local: Celery beat PID $(cat "$RUN_DIR/director-beat.pid") → $RUN_DIR/director-beat.log"

  nohup "$VENV_PY" -m director_api >>"$RUN_DIR/director-api.log" 2>&1 &
  echo $! >"$RUN_DIR/director-api.pid"
  echo "restart-local: API PID $(cat "$RUN_DIR/director-api.pid") → $RUN_DIR/director-api.log"

  sleep 2
  local port="${API_PORT:-8000}"
  if curl -sf "http://127.0.0.1:${port}/v1/health" >/dev/null; then
    echo "restart-local: GET /v1/health ok (port $port)"
  else
    echo "restart-local: warning — /v1/health not ready yet; check $RUN_DIR/director-api.log"
    exit 1
  fi
}

usage() {
  cat <<'EOF'
Stop/start local Directely API + Celery worker + beat (loads repo .env, uses apps/api/.venv).

  ./scripts/restart-local.sh           Stop, then start API + worker + beat in background
  ./scripts/restart-local.sh --stop-only   Stop only
  ./scripts/restart-local.sh --help

Logs & PIDs: .run/director-api.log, .run/director-worker.log, .run/director-beat.log, *.pid
Or: make restart-local

Windows (PowerShell): scripts/restart-local.ps1 — or set CELERY_EAGER=true and run only the API.
EOF
}

main() {
  case "${1:-}" in
    --stop-only)
      stop_all
      echo "restart-local: stopped (did not start)"
      ;;
    --help|-h)
      usage
      ;;
    "")
      stop_all
      start_background
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
}

main "$@"
