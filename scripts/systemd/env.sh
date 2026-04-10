#!/bin/bash
# Shared by Directely systemd wrappers — sources repo-root .env like `make`.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export ROOT
cd "$ROOT/apps/api"
set -a
# shellcheck disable=SC1091
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a
