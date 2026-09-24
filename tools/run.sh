#!/usr/bin/env bash
# Runs a command in the project's environment: `uv run` inside the dev shell
# from flake.nix, which supplies uv, ffmpeg, lua 5.1 and the shared libraries
# the CUDA wheels need. The systemd units, the docs and one-liners all go
# through this, so everything runs against the same pins: flake.lock for the
# tools, .python-version and uv.lock for Python and its packages. Python is
# never invoked without uv.
#
#   ./tools/run.sh tools/generate.py --dry-run
#   ./tools/run.sh fvo-ingest                        # console scripts, see pyproject.toml
#   ./tools/run.sh python -c 'from tools.wowdata import voice_for_npc'
#   ./tools/run.sh luac -p ForeverVO/Core/Util.lua
#
# Without nix (another machine, CI) it is plain `uv run` with ffmpeg from PATH.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
export TQDM_DISABLE=1

if command -v nix >/dev/null 2>&1; then
    exec nix develop "$ROOT" --no-warn-dirty -c uv run "$@"
fi
exec uv run "$@"
