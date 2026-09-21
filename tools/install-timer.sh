#!/usr/bin/env bash
# Installs two systemd user units:
#   forever-vo-ingest.path   sync (pull, ingest, push) the moment the client writes
#                            ForeverVO.lua (on /reload and logout), so no session is lost
#   forever-vo-daily.timer   nightly at 04:00: voice captured lines, continue
#                            the bulk backlog, rebuild the pack tables
# Re-run to update. Set WOW_DIR if the game lives elsewhere.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
WOW_DIR="${WOW_DIR:-$HOME/Faugus/battlenet/drive_c/Program Files (x86)/World of Warcraft}"
ACCOUNTS="$WOW_DIR/_classic_beta_/WTF/Account"
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
OnCalendar=*-*-* 04:00:00
Persistent=true
RandomizedDelaySec=10m

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
ExecStart=/run/current-system/sw/bin/systemd-inhibit --what=idle --mode=block --who="Forever Voiceover" --why="bulk voice generation" $ROOT/tools/run.sh tools/generate.py
Restart=on-failure
RestartSec=60
Nice=10

[Install]
WantedBy=default.target
UNIT

{
    echo "[Unit]"
    echo "Description=Forever Voiceover: watch saved variables for writes"
    echo
    echo "[Path]"
    for account in "$ACCOUNTS"/*/; do
        # account folders look like 123456789#1; skip the account-wide SavedVariables dir itself
        [ -d "${account}SavedVariables" ] || continue
        echo "PathModified=${account}SavedVariables/ForeverVO.lua"
    done
    echo "Unit=forever-vo-ingest.service"
    echo
    echo "[Install]"
    echo "WantedBy=default.target"
} >"$UNIT_DIR/forever-vo-ingest.path"

systemctl --user daemon-reload
systemctl --user enable --now forever-vo-daily.timer
systemctl --user enable --now forever-vo-ingest.path
# Enabled but not started here: a reboot then resumes the bulk backlog on its
# own (the run is resumable and skips files that already exist).
systemctl --user enable forever-vo-bulk.service
systemctl --user list-timers forever-vo-daily.timer --no-pager
echo "watching:"
grep PathModified "$UNIT_DIR/forever-vo-ingest.path"
echo "logs: tools/data/ingest.log and tools/data/daily.log; run the nightly job now with: systemctl --user start forever-vo-daily.service"
echo "bulk generation: systemctl --user start forever-vo-bulk.service ; progress: journalctl --user -u forever-vo-bulk -f"
