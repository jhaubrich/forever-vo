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
cd "$ROOT"
# Sync with GitHub (pull, ingest, push) whatever else is going on
./tools/ingest.sh || true

# The bulk service now starts at boot, so it is normally running when this job
# fires. Stop it for the duration: this run needs the GPU and sole ownership of
# sound_index.json, and bailing out instead would skip the captured-line pass,
# the table rebuild and the delta pack upload for as long as bulk stays up.
BULK_WAS_ACTIVE=0
if systemctl --user is-active --quiet forever-vo-bulk.service; then
    BULK_WAS_ACTIVE=1
    echo "stopping forever-vo-bulk.service for the duration of this run"
    systemctl --user stop forever-vo-bulk.service || true
fi
restore_bulk() {
    if [ "$BULK_WAS_ACTIVE" = 1 ]; then
        echo "restarting forever-vo-bulk.service"
        systemctl --user start forever-vo-bulk.service || true
    fi
}
trap restore_bulk EXIT

# A generate.py that is not the service (a manual run) still makes us stand down.
if pgrep -f 'tools/generate.py' >/dev/null; then
    echo "a manual generate.py is running; leaving generation to it"
    exit 0
fi
./tools/run.sh tools/generate.py --captured --progress 2>&1 | grep -v -i -E 'warn|deprecat|pkg_resources|^\s*$|Sampling|self.gen|sdpa' || true

# Then continue the bulk backlog for a while (timeout returns 124 when it cuts the run short)
timeout "${BULK_HOURS}h" ./tools/run.sh tools/generate.py 2>&1 | grep -v -i -E 'warn|deprecat|pkg_resources|^\s*$|Sampling|self.gen|sdpa' || true
./tools/run.sh tools/generate.py --tables-only 2>&1 | tail -1 || true

# Publish the Forever delta pack to CurseForge when it is worth an update: 20+ new files, or a week with any change
./tools/run.sh tools/release_pack.py delta --upload --if-changed --min-new 20 --max-age-days 7 2>&1 | grep -v -i -E 'warn|Installed' | tail -2 || true

# Publish the text side of the build so the repository matches this machine
git add tools/data/capture.json tools/data/sound_index.json tools/data/bulk/questcache.json ForeverVO_Data/Data captures 2>/dev/null || true
if ! git diff --cached --quiet; then
    git commit -q -m "nightly: $(date +%F) captures and pack tables" && git push -q || echo "git push failed"
fi
echo "=== $(date -Is) done ==="
