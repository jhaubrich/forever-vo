#!/usr/bin/env bash
# Sync step: pull what the GitHub bot committed, ingest the client's saved
# variables and any community captures into tools/data/capture.json, and push
# the result. Runs under a lock so the on-write watcher, the periodic timer
# and the nightly job never overlap.
#
# Triggered by forever-vo-ingest.path (client wrote ForeverVO.lua) and
# forever-vo-sync.timer (every 20 minutes). Also called by daily.sh.
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
echo "=== $(date -Is) sync ==="

git pull -q --rebase --autostash origin main || echo "git pull failed; continuing with local data"
./tools/run.sh tools/ingest.py "$@" || true

git add tools/data/capture.json captures 2>/dev/null || true
if ! git diff --cached --quiet; then
    git commit -q -m "sync: $(date '+%F %H:%M') captures" && git push -q origin main || echo "git push failed"
fi
