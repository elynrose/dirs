#!/usr/bin/env bash
# Directely — full local stack (macOS / Linux). Opens Studio in your default browser.
#
# Usage:
#   chmod +x Launch.sh   # once
#   ./Launch.sh
#   ./Launch.sh --skip-browser
#   ./Launch.sh --skip-docker --skip-vite
#
# Flags (also accept -SkipDocker style for parity with Launch.ps1):
#   --skip-docker, --skip-migrate, --skip-browser, --skip-beat, --skip-bootstrap, --skip-vite

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOTENV="$ROOT/.env"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
RUN_DIR="$ROOT/.run"
VENV_PY="$API_DIR/.venv/bin/python"
VENV_CELERY="$API_DIR/.venv/bin/celery"
DOCKER_WAIT_SEC="${DOCKER_WAIT_SEC:-120}"

SKIP_DOCKER=false
SKIP_MIGRATE=false
SKIP_BROWSER=false
SKIP_BEAT=false
SKIP_BOOTSTRAP=false
SKIP_VITE=false

usage() {
  cat <<'EOF'
./Launch.sh [--skip-docker] [--skip-migrate] [--skip-browser] [--skip-beat] [--skip-bootstrap] [--skip-vite]
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --skip-docker|-SkipDocker) SKIP_DOCKER=true ;;
    --skip-migrate|-SkipMigrate) SKIP_MIGRATE=true ;;
    --skip-browser|-SkipBrowser) SKIP_BROWSER=true ;;
    --skip-beat|-SkipBeat) SKIP_BEAT=true ;;
    --skip-bootstrap|-SkipBootstrap) SKIP_BOOTSTRAP=true ;;
    --skip-vite|-SkipVite) SKIP_VITE=true ;;
    *)
      echo "Unknown option: $arg" >&2
      usage
      exit 1
      ;;
  esac
done

have_cmd() { command -v "$1" >/dev/null 2>&1; }

docker_ready() {
  docker info >/dev/null 2>&1
}

wait_docker() {
  local deadline=$((SECONDS + DOCKER_WAIT_SEC))
  until docker_ready; do
    if (( SECONDS > deadline )); then
      echo "Docker did not become ready within ${DOCKER_WAIT_SEC}s. Start Docker Desktop, then retry." >&2
      exit 1
    fi
    sleep 2
  done
}

open_browser() {
  local url="${1:-http://localhost:5173/}"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    open "$url"
  elif have_cmd xdg-open; then
    xdg-open "$url" >/dev/null 2>&1 || true
  else
    echo "Open manually: $url"
  fi
}

if [[ "$SKIP_BOOTSTRAP" != true ]]; then
  if [[ ! -x "$VENV_PY" ]]; then
    cat <<EOF >&2
Missing Python venv: $VENV_PY

  cd apps/api && python3.11 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

Then run ./Launch.sh again (or use --skip-bootstrap if the venv exists elsewhere).
EOF
    exit 1
  fi
  if ! "$VENV_PY" -c "from ffmpeg_pipelines.audio_slot import normalize_audio_to_duration; import director_api" 2>/dev/null; then
    echo "Installing / repairing Python deps in venv…"
    ( cd "$API_DIR" && "$VENV_PY" -m pip install -e ".[dev]" )
  fi
fi

if [[ ! -x "$VENV_PY" ]]; then
  echo "Python venv not executable: $VENV_PY" >&2
  exit 1
fi

if [[ "$SKIP_DOCKER" != true ]]; then
  have_cmd docker || { echo "Docker CLI not found. Install Docker Desktop and retry." >&2; exit 1; }
  if ! docker_ready; then
    echo "Starting Docker…"
    if [[ "$(uname -s)" == "Darwin" ]]; then
      open -a Docker 2>/dev/null || true
    fi
    wait_docker
  fi
  echo "Starting Compose stack (Postgres, Redis, MinIO)…"
  ( cd "$ROOT" && docker compose up -d --wait ) || {
    echo "docker compose --wait failed; retrying without --wait…"
    ( cd "$ROOT" && docker compose up -d )
    sleep 8
  }
fi

if [[ "$SKIP_VITE" != true ]]; then
  have_cmd node && have_cmd npm || { echo "Node.js / npm required for the Studio UI, or use --skip-vite." >&2; exit 1; }
  if [[ ! -d "$WEB_DIR/node_modules" ]]; then
    echo "Web: npm install (first run)…"
    ( cd "$WEB_DIR" && npm install )
  fi
fi

if [[ "$SKIP_MIGRATE" != true ]]; then
  echo "Running database migrations…"
  ( cd "$API_DIR" && "$VENV_PY" -m alembic upgrade head )
fi

mkdir -p "$RUN_DIR"

if [[ ! -x "$VENV_CELERY" ]]; then
  echo "Missing $VENV_CELERY — reinstall apps/api venv with pip install -e '.[dev]'" >&2
  exit 1
fi

echo "Starting API, Celery worker$([[ "$SKIP_BEAT" == true ]] || echo ", Celery beat")$([[ "$SKIP_VITE" == true ]] || echo ", Vite")…"

load_repo_env() {
  set -a
  # shellcheck source=/dev/null
  [[ -f "$DOTENV" ]] && . "$DOTENV"
  set +a
}

(
  cd "$API_DIR"
  load_repo_env
  nohup "$VENV_CELERY" -A director_api.tasks.celery_app worker -Q text,media,compile -l info >>"$RUN_DIR/director-worker.log" 2>&1 &
  echo $! >"$RUN_DIR/director-worker.pid"
)

if [[ "$SKIP_BEAT" != true ]]; then
  (
    cd "$API_DIR"
    load_repo_env
    nohup "$VENV_CELERY" -A director_api.tasks.celery_app beat -l info >>"$RUN_DIR/director-beat.log" 2>&1 &
    echo $! >"$RUN_DIR/director-beat.pid"
  )
fi

(
  cd "$API_DIR"
  load_repo_env
  export API_RELOAD="${API_RELOAD:-0}"
  nohup "$VENV_PY" -m director_api >>"$RUN_DIR/director-api.log" 2>&1 &
  echo $! >"$RUN_DIR/director-api.pid"
)

if [[ "$SKIP_VITE" != true ]]; then
  nohup bash -c "cd '$WEB_DIR' && exec npm run dev" >>"$RUN_DIR/director-vite.log" 2>&1 &
  echo $! >"$RUN_DIR/director-vite.pid"
fi

sleep 2

if [[ "$SKIP_BROWSER" != true && "$SKIP_VITE" != true ]]; then
  open_browser "http://localhost:5173/"
fi

echo ""
echo "Directely is starting."
[[ "$SKIP_VITE" != true ]] && echo "  Web UI:  http://localhost:5173/"
echo "  API:     http://127.0.0.1:8000/v1/health"
echo "  Logs:    $RUN_DIR/director-*.log"
echo "  Stop:    ./scripts/restart-local.sh --stop-only  (Unix)  or  scripts/stop-director.ps1  (Windows)"
