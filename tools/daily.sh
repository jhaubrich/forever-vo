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
# The run starts at 02:30 (install-timer.sh) and the GPU is free from END_AT,
# for the owner's WoW client. Generation stops there; what follows (table
# rebuild, dry run, re-encoding and uploading the packs, push) is CPU and
# network only, at Nice=10, and may run past it: a new encoding or a re-voiced
# set re-encodes every pack, about an hour of ffmpeg.
END_AT="${FOREVER_VO_END_AT:-07:00}"
BULK_HOURS="${FOREVER_VO_BULK_HOURS:-}"   # optional cap on the bulk pass; unset, it fills the window

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
# Generation stops at END_AT. A run that starts too late for that (the timer's catch-up after the machine was off at 02:30) gets two hours,
# the old fixed bound, so a daytime catch-up does not hold the GPU all day.
GEN_UNTIL="$(date -d "today $END_AT" +%s)"
if [ "$GEN_UNTIL" -le "$(date +%s)" ]; then
    GEN_UNTIL="$(date -d "now 2 hours" +%s)"
    echo "started after the window; generating until $(date -d "@$GEN_UNTIL" +%H:%M)"
fi
if [ -n "$BULK_HOURS" ]; then
    CAP="$(date -d "now $BULK_HOURS hours" +%s)"
    if [ "$CAP" -lt "$GEN_UNTIL" ]; then
        GEN_UNTIL="$CAP"
    fi
fi
# Seconds of generation left before GEN_UNTIL, at least 1: `timeout 0` means
# no limit at all
remaining() {
    local left=$((GEN_UNTIL - $(date +%s)))
    echo $((left > 1 ? left : 1))
}
# Sync with GitHub (pull, ingest, push) whatever else is going on
./tools/ingest.sh || true

# The bulk service now starts at boot, so it is normally running when this job
# fires. Stop it for the duration: this run needs the GPU and sole ownership of
# sound_index.json, and bailing out instead would skip the captured-line pass,
# the table rebuild and the pack uploads for as long as bulk stays up.
BULK_WAS_ACTIVE=0
if systemctl --user is-active --quiet forever-vo-bulk.service; then
    BULK_WAS_ACTIVE=1
    echo "stopping forever-vo-bulk.service for the duration of this run"
    systemctl --user stop forever-vo-bulk.service || true
fi
# The bulk service exits 0 once its todo list is empty and only a login starts
# it again, so work that appears later (a rebuilt reference clip, a voice
# change, a merged pipeline branch) would otherwise get only this job's
# window a night. Restart it when it was running before, or when a backlog
# is still there at the end of this run.
BULK_PENDING=0
restore_bulk() {
    if [ "$BULK_WAS_ACTIVE" = 1 ] || [ "$BULK_PENDING" -gt 0 ]; then
        echo "starting forever-vo-bulk.service ($BULK_PENDING files still to generate)"
        systemctl --user start forever-vo-bulk.service || true
    fi
}
trap restore_bulk EXIT

# A generate.py that is not the service (a manual run) still makes us stand down.
if pgrep -f 'tools/generate.py' >/dev/null; then
    echo "a manual generate.py is running; leaving generation to it"
    exit 0
fi
# As many shards as the bulk service runs (its workers.conf drop-in), so one
# knob sets both and a night with the game closed gets two workers throughout
if [ -z "${FOREVER_VO_WORKERS:-}" ]; then
    FOREVER_VO_WORKERS="$(systemctl --user show forever-vo-bulk.service -p Environment --value |
        tr ' ' '\n' | sed -n 's/^FOREVER_VO_WORKERS=//p' | tail -1)"
fi
export FOREVER_VO_WORKERS="${FOREVER_VO_WORKERS:-1}"
# Captured lines first, then the bulk backlog until GEN_UNTIL (timeout returns
# 124 when it cuts a pass short, and bulk.sh passes the stop on to its shards;
# files already written are kept)
echo "generating until $(date -d "@$GEN_UNTIL" +%H:%M) with $FOREVER_VO_WORKERS worker(s)"
timeout "$(remaining)" ./tools/bulk.sh --captured --progress 2>&1 | grep -v -i -E 'warn|deprecat|pkg_resources|^\s*$|Sampling|self.gen|sdpa' || true
if [ "$(remaining)" -gt 60 ]; then
    timeout "$(remaining)" ./tools/bulk.sh 2>&1 | grep -v -i -E 'warn|deprecat|pkg_resources|^\s*$|Sampling|self.gen|sdpa' || true
fi
./tools/run.sh tools/generate.py --tables-only 2>&1 | tail -1 || true
# What the timed run left behind, from the same todo list it walked (a dry run
# takes a few seconds). The EXIT trap hands it to the bulk service.
BULK_PENDING="$(./tools/run.sh tools/generate.py --dry-run 2>/dev/null | sed -n 's/^\([0-9]\+\) files to generate.*/\1/p' | head -1)"
BULK_PENDING="${BULK_PENDING:-0}"
echo "backlog after this run: $BULK_PENDING files"

# Publish to CurseForge when each pack is worth an update: every pack goes
# whole through the API once enough of its files are new, gone or changed (a
# re-voiced line counts), or after a week with any change, so the last few
# files of a re-voicing do not wait for unrelated ones. A build re-encodes
# only what changed. A pack the
# API refuses is recorded all the same and must be uploaded by hand soon, so
# say so on the desktop; the tail keeps release_pack's instructions in this log.
# One whose upload failed on the way (the Wi-Fi card) is uploaded again the
# next night; say that too, so a failure there is not a surprise.
for args in "classic_quests --min-new 20" "classic_endgame --min-new 20" "classic_gossip --min-new 20" \
    "forever_quests --min-new 20" "forever_gossip --min-new 20" "books --min-new 10"; do
    args="$args --max-age-days 7"
    # shellcheck disable=SC2086
    OUT="$(./tools/run.sh tools/release_pack.py $args --upload --if-changed 2>&1 | grep -v -i -E 'warn|Installed' | tail -12 || true)"
    echo "$OUT"
    REFUSED="$(printf '%s\n' "$OUT" | sed -n 's/^upload by hand: \(.*\) (details above)$/\1/p')"
    RETRY="$(printf '%s\n' "$OUT" | sed -n 's/^upload failed, retried next run: \(.*\) (details above)$/\1/p')"
    if [ -n "$RETRY" ]; then
        /run/current-system/sw/bin/gdbus call --session --dest org.freedesktop.Notifications \
            --object-path /org/freedesktop/Notifications \
            --method org.freedesktop.Notifications.Notify \
            "Forever VO" 0 "dialog-information" "Upload failed: $RETRY" \
            "The connection failed after retries; tomorrow night's run uploads it again. Details in tools/data/daily.log." \
            "[]" "{}" 0 >/dev/null 2>&1 || true
    fi
    if [ -n "$REFUSED" ]; then
        /run/current-system/sw/bin/gdbus call --session --dest org.freedesktop.Notifications \
            --object-path /org/freedesktop/Notifications \
            --method org.freedesktop.Notifications.Notify \
            "Forever VO" 0 "dialog-warning" "Upload by hand: $REFUSED" \
            "CurseForge's API refused it. The zip and the form fields are in tools/data/daily.log." \
            "[]" "{'urgency':<byte 1>}" 0 >/dev/null 2>&1 || true
    fi
done

# Publish the text side of the build so the repository matches this machine
git add tools/data/capture.json tools/data/sound_index.json ForeverVO_Data/Data captures 2>/dev/null || true
if ! git diff --cached --quiet; then
    # The pull at the start was hours ago and the GitHub bot commits captures
    # meanwhile, so rebase onto them first or the push is refused
    git commit -q -m "nightly: $(date +%F) captures and pack tables" &&
        { git pull -q --rebase --autostash origin main || { git rebase --abort 2>/dev/null; false; }; } &&
        git push -q origin main || echo "git push failed"
fi
echo "=== $(date -Is) done ==="
