#!/usr/bin/env bash
# Daily job: pull in whatever the addon captured, voice the lines that still
# have no audio (captured ones first, then keep chipping at the bulk backlog for
# a bounded time), and rebuild the pack tables.
#
# Installed as a systemd user timer by tools/install-timer.sh. Safe to run by
# hand: ./tools/daily.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/tools/data/daily.log"
LOCK="$ROOT/tools/data/daily.lock"
BULK_HOURS="${FOREVER_VO_BULK_HOURS:-2}"   # how long the daily run may spend on the bulk backlog

mkdir -p "$ROOT/tools/data"
exec >>"$LOG" 2>&1
echo "=== $(date -Is) daily run ==="

# Skip if another generation (daily or manual bulk) is still running
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "another run holds the lock; skipping"
    exit 0
fi
if pgrep -f 'tools/generate.py' >/dev/null; then
    echo "a generate.py process is already running; skipping"
    exit 0
fi

cd "$ROOT"
# Pick up captures the GitHub bot committed from player exports
git pull --rebase --quiet || echo "git pull failed; continuing with local data"
./tools/run.sh tools/ingest.py || true
./tools/run.sh tools/generate.py --captured --progress 2>&1 | grep -v -i -E 'warn|deprecat|pkg_resources|^\s*$|Sampling|self.gen|sdpa' || true

# Then continue the bulk backlog for a while (timeout returns 124 when it cuts the run short)
timeout "${BULK_HOURS}h" ./tools/run.sh tools/generate.py 2>&1 | grep -v -i -E 'warn|deprecat|pkg_resources|^\s*$|Sampling|self.gen|sdpa' || true
./tools/run.sh tools/generate.py --tables-only 2>&1 | tail -1 || true
echo "=== $(date -Is) done ==="
