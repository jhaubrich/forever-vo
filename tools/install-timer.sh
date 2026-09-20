#!/usr/bin/env bash
# Installs a systemd user timer that runs tools/daily.sh every day at 04:00
# (and on the next boot if the machine was off). Re-run to update.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat >"$UNIT_DIR/forever-vo-daily.service" <<EOF
[Unit]
Description=Forever Voiceover: ingest captured lines and generate audio

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=PATH=/run/wrappers/bin:$HOME/.nix-profile/bin:/etc/profiles/per-user/$USER/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin
Environment=HOME=$HOME
ExecStart=$ROOT/tools/daily.sh
Nice=10
EOF

cat >"$UNIT_DIR/forever-vo-daily.timer" <<EOF
[Unit]
Description=Forever Voiceover daily generation

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now forever-vo-daily.timer
systemctl --user list-timers forever-vo-daily.timer --no-pager
echo "run it now with: systemctl --user start forever-vo-daily.service ; logs in tools/data/daily.log"
