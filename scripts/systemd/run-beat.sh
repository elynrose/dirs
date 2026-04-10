#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"
"$SCRIPT_DIR/wait-for-postgres.sh"
exec .venv/bin/celery -A director_api.tasks.celery_app beat -l info
