#!/usr/bin/env bash
# Ingests the addon's saved variables into tools/data/capture.json, serialised
# with a lock so the on-write watcher and the nightly job never overlap.
# Triggered by the forever-vo-ingest.path unit whenever the client writes
# WTF/Account/*/SavedVariables/ForeverVO.lua (on /reload and logout).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/tools/data/ingest.log"
LOCK="$ROOT/tools/data/ingest.lock"
mkdir -p "$ROOT/tools/data"
exec >>"$LOG" 2>&1

# The client may still be writing when the watcher fires; let it finish
sleep "${FOREVER_VO_INGEST_DELAY:-3}"

exec 9>"$LOCK"
flock -w 120 9
cd "$ROOT"
echo "=== $(date -Is) ingest ==="
./tools/run.sh tools/ingest.py "$@"
