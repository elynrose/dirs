#!/usr/bin/env bash
set -euo pipefail

# Compatibility wrapper: user shorthand prefers underscore filename.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/restart-local.sh" "$@"
