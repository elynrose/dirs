#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"
"$SCRIPT_DIR/wait-for-postgres.sh"
# Must consume text, media, and compile — tasks are routed off the implicit "celery" queue.
# See apps/api/director_api/tasks/celery_app.py (task_routes / task_default_queue).
exec .venv/bin/celery -A director_api.tasks.celery_app worker -Q text,media,compile -l info
