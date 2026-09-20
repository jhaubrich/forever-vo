#!/usr/bin/env bash
# Installs two systemd user units:
#   forever-vo-ingest.path   ingest the moment the client writes ForeverVO.lua
#                            (on /reload and logout), so no session is lost
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

# Periodic sync so captures committed by the GitHub bot reach this machine quickly
cat >"$UNIT_DIR/forever-vo-sync.timer" <<UNIT
[Unit]
Description=Forever Voiceover periodic sync with GitHub

[Timer]
OnBootSec=2m
OnUnitActiveSec=20m
Unit=forever-vo-ingest.service

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now forever-vo-daily.timer
systemctl --user enable --now forever-vo-ingest.path
systemctl --user enable --now forever-vo-sync.timer
systemctl --user list-timers 'forever-vo-*' --no-pager
echo "watching:"
grep PathModified "$UNIT_DIR/forever-vo-ingest.path"
echo "logs: tools/data/ingest.log and tools/data/daily.log; run the nightly job now with: systemctl --user start forever-vo-daily.service"
