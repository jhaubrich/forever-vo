#!/usr/bin/env bash
# Installs systemd user units:
#   forever-vo-daily.timer   nightly from 02:30, done by 07:00 (daily.sh): sync
#                            (pull, ingest, push), voice captured lines, continue
#                            the bulk backlog, rebuild the pack tables
#   forever-vo-ingest.service the sync alone, started by hand when wanted
#   forever-vo-bulk.service  the resumable bulk run
# Until 2026-09-29 a path unit also synced on every write of ForeverVO.lua,
# because this beta did not read saved variables back and each logout
# overwrote the last session. They persist now and the capture DB only grows,
# so the nightly sync loses nothing; a re-run removes the old watcher.
# Re-run to update.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_PATH="/run/wrappers/bin:$HOME/.nix-profile/bin:/etc/profiles/per-user/$USER/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin"
mkdir -p "$UNIT_DIR"

cat >"$UNIT_DIR/forever-vo-daily.service" <<UNIT
[Unit]
Description=Forever Voiceover: nightly voice generation

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=PATH=$UNIT_PATH
Environment=HOME=$HOME
ExecStart=$ROOT/tools/daily.sh
Nice=10
UNIT

cat >"$UNIT_DIR/forever-vo-daily.timer" <<UNIT
[Unit]
Description=Forever Voiceover nightly generation

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

cat >"$UNIT_DIR/forever-vo-ingest.service" <<UNIT
[Unit]
Description=Forever Voiceover: sync (pull, ingest saved variables, push)

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=PATH=$UNIT_PATH
Environment=HOME=$HOME
ExecStart=$ROOT/tools/ingest.sh
UNIT

# Long-running bulk generation. Each file is written as it finishes and existing
# files are skipped on start, so it survives crashes (a CUDA context lost to
# suspend, for one) by simply restarting where it left off.
#
# tools/bulk.sh runs several generate.py shards; one stream leaves the GPU ~60%
# idle (1.68x realtime against 2.28x for two).
#
# systemd-inhibit --what=idle holds off the idle suspend that would otherwise
# take the machine down overnight mid-run (the GPU loses its CUDA context, the
# run dies and resumes only when someone wakes the box). Only the *idle* timer
# is blocked: closing the lid or suspending by hand still works.
cat >"$UNIT_DIR/forever-vo-bulk.service" <<UNIT
[Unit]
Description=Forever Voiceover: bulk voice generation (resumable)

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PATH=$UNIT_PATH
Environment=HOME=$HOME
ExecStart=/run/current-system/sw/bin/systemd-inhibit --what=idle --mode=block --who="Forever Voiceover" --why="bulk voice generation" $ROOT/tools/bulk.sh
Restart=on-failure
RestartSec=60
Nice=10

[Install]
WantedBy=default.target
UNIT

if [ -e "$UNIT_DIR/forever-vo-ingest.path" ]; then
    systemctl --user disable --now forever-vo-ingest.path || true
    rm -f "$UNIT_DIR/forever-vo-ingest.path"
fi

systemctl --user daemon-reload
systemctl --user enable --now forever-vo-daily.timer
# Enabled but not started here: a reboot then resumes the bulk backlog on its
# own (the run is resumable and skips files that already exist).
systemctl --user enable forever-vo-bulk.service
systemctl --user list-timers forever-vo-daily.timer --no-pager
echo "logs: tools/data/ingest.log and tools/data/daily.log; run the nightly job now with: systemctl --user start forever-vo-daily.service; sync alone: systemctl --user start forever-vo-ingest.service"
echo "bulk generation: systemctl --user start forever-vo-bulk.service ; progress: journalctl --user -u forever-vo-bulk -f"
