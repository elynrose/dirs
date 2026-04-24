#!/usr/bin/env bash
# Fill a *small* filesystem to provoke ENOSPC on writes (destructive to that mount only).
#
#   export STRESS_DESTRUCTIVE=1
#   export STRESS_FILL_DIR=/tmp/director-stress-mnt   # must exist; use a tiny tmpfs or loop mount
#   bash apps/api/scripts/stress/fill_disk_small_mount.sh
#
# Then point LOCAL_STORAGE_ROOT at STRESS_FILL_DIR and trigger an export, or run worker tests.
# Do NOT point at production volumes.

set -euo pipefail

if [[ "${STRESS_DESTRUCTIVE:-}" != "1" ]]; then
  echo "Refusing to run: set STRESS_DESTRUCTIVE=1" >&2
  exit 2
fi

DIR="${STRESS_FILL_DIR:-}"
if [[ -z "$DIR" || ! -d "$DIR" ]]; then
  echo "Set STRESS_FILL_DIR to an existing directory on a small test mount" >&2
  exit 2
fi

echo "Filling $DIR (best-effort) ..."
# Prefer fallocate; fall back to dd
if command -v fallocate >/dev/null 2>&1; then
  i=0
  while fallocate -l 16M "$DIR/stress_$i.bin" 2>/dev/null; do
    i=$((i + 1))
    if [[ $i -gt 100000 ]]; then
      echo "Safety stop at 100000 chunks" >&2
      break
    fi
  done
else
  i=0
  while dd if=/dev/zero of="$DIR/stress_$i.bin" bs=1048576 count=16 2>/dev/null; do
    i=$((i + 1))
    if [[ $i -gt 100000 ]]; then
      break
    fi
  done
fi

echo "Done (disk may be full). Remove $DIR/stress_*.bin when finished."
