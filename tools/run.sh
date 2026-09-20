#!/usr/bin/env bash
# Runs a tools/ script with `uv run`, which installs each script's dependencies
# from its inline metadata (the "# /// script" block) into uv's cache.
#
#   ./tools/run.sh tools/generate.py --dry-run
#   ./tools/run.sh tools/ingest.py
#   ./tools/run.sh python -c 'print(1)'        # plain interpreter, no extra packages
#
# On NixOS the manylinux wheels (numpy, torch, ...) also need libstdc++, zlib and
# the system libcuda on the library path, and uv must use the nix-provided
# Python rather than downloading one, so this wrapper sets that up.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v nix >/dev/null 2>&1; then
    LIBS=""
    for pkg in stdenv.cc.cc.lib zlib; do
        out="$(nix build --no-link --print-out-paths "nixpkgs#$pkg")"
        LIBS="${LIBS:+$LIBS:}$out/lib"
    done
    export LD_LIBRARY_PATH="$LIBS:/run/opengl-driver/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export UV_PYTHON_PREFERENCE=only-system
    export PYTHONUNBUFFERED=1
    export TQDM_DISABLE=1
    exec nix shell nixpkgs#python312 nixpkgs#uv nixpkgs#ffmpeg -c sh -c '
        case "$1" in
            *.py) exec uv run "$@" ;;
            *)    exec uv run --no-project "$@" ;;
        esac' sh "$@"
fi

export PYTHONUNBUFFERED=1
export TQDM_DISABLE=1
case "$1" in
    *.py) exec uv run "$@" ;;
    *)    exec uv run --no-project "$@" ;;
esac
