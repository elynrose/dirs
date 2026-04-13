#!/usr/bin/env bash
# Run on your **Linux web server** after pulling or merging major changes (see INSTALLATION.md §4–7).
#
# Typical flow:
#   cd /opt/director   # or /root/director
#   ./scripts/server-major-update.sh
#
# What it does by default:
#   1. git pull --rebase (branch: main, or GIT_BRANCH / --branch)
#   2. pip install -e ".[dev]" in apps/api/.venv
#   3. alembic upgrade head (via make migrate, or direct if make is missing)
#   4. npm ci && npm run build in apps/web
#   5. rsync apps/web/dist → DIRECTOR_STATIC_WEB_ROOT (default /var/www/directely) with sudo
#   6. sudo systemctl restart director-api director-worker director-beat nginx
#
# Requires: git, Docker/Postgres up for migrations, Node/npm, sudo for rsync + systemd + nginx.
# Optional: --with-vite to restart director-vite (dev-style UI; skip if you only serve static dist).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${DIRECTOR_REPO:-$ROOT}"
API_DIR="$REPO/apps/api"
WEB_DIR="$REPO/apps/web"
VENV_PIP="$API_DIR/.venv/bin/pip"
VENV_ALEMBIC="$API_DIR/.venv/bin/alembic"
DOTENV="$REPO/.env"

BRANCH="${GIT_BRANCH:-main}"
REMOTE="${GIT_REMOTE:-origin}"
STATIC_WEB_ROOT="${DIRECTOR_STATIC_WEB_ROOT:-/var/www/directely}"

DO_GIT=1
DO_BACKEND=1
DO_WEB=1
DO_RSYNC=1
DO_SYSTEMD=1
DO_NGINX=1
DO_VITE=0

usage() {
  cat <<'EOF'
server-major-update.sh — pull code, refresh backend, build web, publish static, restart services.

Default steps:
  git pull --rebase → pip install → alembic → npm ci/build → rsync dist → systemctl + nginx

Options:
  --branch NAME     Git branch to pull (default: main, or GIT_BRANCH env)
  --no-git          Skip git fetch/pull
  --no-backend      Skip pip install and database migrations
  --no-web          Skip npm ci / npm run build
  --no-rsync        Build web but do not rsync to DIRECTOR_STATIC_WEB_ROOT
  --no-systemd      Do not restart director-api, director-worker, director-beat
  --no-nginx        Do not restart nginx (still restarts director-* unless --no-systemd)
  --with-vite       Also restart director-vite (only if you use the Vite systemd unit)
  -h, --help        This help

Environment:
  DIRECTOR_REPO             Repo path (default: directory containing this script)
  DIRECTOR_STATIC_WEB_ROOT    Static web root for rsync (default: /var/www/directely)
  GIT_BRANCH, GIT_REMOTE      Branch and remote for git pull

Examples:
  ./scripts/server-major-update.sh
  DIRECTOR_STATIC_WEB_ROOT=/var/www/html ./scripts/server-major-update.sh --branch main
  ./scripts/server-major-update.sh --no-git --no-rsync    # already pulled; only build + restart API
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      BRANCH="${2:?--branch requires a name}"
      shift 2
      ;;
    --no-git) DO_GIT=0; shift ;;
    --no-backend) DO_BACKEND=0; shift ;;
    --no-web) DO_WEB=0; shift ;;
    --no-rsync) DO_RSYNC=0; shift ;;
    --no-systemd) DO_SYSTEMD=0; shift ;;
    --no-nginx) DO_NGINX=0; shift ;;
    --with-vite) DO_VITE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

load_env() {
  set -a
  # shellcheck source=/dev/null
  [[ -f "$DOTENV" ]] && . "$DOTENV"
  set +a
}

run_migrate() {
  if command -v make >/dev/null 2>&1; then
    make -C "$REPO" migrate
  else
    (
      load_env
      cd "$API_DIR"
      "$VENV_ALEMBIC" upgrade head
    )
  fi
}

echo "==> Directely server major update (repo: $REPO)"

if [[ ! -d "$REPO/.git" ]]; then
  echo "error: not a git repo: $REPO" >&2
  exit 1
fi

if [[ "$DO_GIT" -eq 1 ]]; then
  echo "==> git fetch && pull --rebase $REMOTE $BRANCH"
  git -C "$REPO" fetch "$REMOTE"
  git -C "$REPO" pull --rebase "$REMOTE" "$BRANCH"
fi

if [[ "$DO_BACKEND" -eq 1 ]]; then
  [[ -x "$VENV_PIP" ]] || {
    echo "error: missing $VENV_PIP — create venv: cd apps/api && python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\"" >&2
    exit 1
  }
  echo "==> pip install -e .[dev] (apps/api)"
  (cd "$API_DIR" && "$VENV_PIP" install -e ".[dev]")

  echo "==> database migrations"
  run_migrate
fi

if [[ "$DO_WEB" -eq 1 ]]; then
  command -v npm >/dev/null 2>&1 || {
    echo "error: npm not found" >&2
    exit 1
  }
  echo "==> npm ci && npm run build (apps/web)"
  (cd "$WEB_DIR" && npm ci && npm run build)
fi

if [[ "$DO_RSYNC" -eq 1 ]]; then
  if [[ "$DO_WEB" -ne 1 ]]; then
    echo "warning: --no-web with rsync enabled — using existing $WEB_DIR/dist if present" >&2
  fi
  [[ -d "$WEB_DIR/dist" ]] || {
    echo "error: $WEB_DIR/dist missing — run web build or drop --no-web" >&2
    exit 1
  }
  echo "==> rsync dist → $STATIC_WEB_ROOT (sudo)"
  sudo mkdir -p "$STATIC_WEB_ROOT"
  sudo rsync -a --delete "$WEB_DIR/dist/" "$STATIC_WEB_ROOT/"
  sudo chown -R www-data:www-data "$STATIC_WEB_ROOT"
fi

if [[ "$DO_SYSTEMD" -eq 1 ]] || [[ "$DO_NGINX" -eq 1 ]] || [[ "$DO_VITE" -eq 1 ]]; then
  RESTART_CMD=(sudo systemctl restart)
  UNITS=()
  if [[ "$DO_SYSTEMD" -eq 1 ]]; then
    UNITS+=(director-api director-worker director-beat)
  fi
  if [[ "$DO_VITE" -eq 1 ]]; then
    UNITS+=(director-vite)
  fi
  if [[ ${#UNITS[@]} -gt 0 ]]; then
    echo "==> systemctl restart ${UNITS[*]}"
    sudo systemctl restart "${UNITS[@]}"
  fi
  if [[ "$DO_NGINX" -eq 1 ]]; then
    echo "==> systemctl restart nginx"
    sudo systemctl restart nginx
  fi
fi

echo "==> done."
if [[ "$DO_SYSTEMD" -eq 1 ]]; then
  port="${API_PORT:-8000}"
  if curl -sf "http://127.0.0.1:${port}/v1/health" >/dev/null 2>&1; then
    echo "    GET /v1/health ok (port $port)"
  else
    echo "    warning: /v1/health not ready — check: journalctl -u director-api -n 50 --no-pager" >&2
  fi
fi
