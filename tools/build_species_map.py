"""Maps creature model files to a species name, so speakers with no player race
can still be given a voice of their own kind.

CreatureDisplayInfoExtra only exists for player-race models, so a dryad, an ogre
or a child resolves to no race and borrows a human's voice. The model file itself
names the species - CreatureModelData.FileDataID points at
creature/<species>/<species>.m2 - which is enough to pick dryad-female.wav over
human-female.wav.

The community listfile is 70 MB, so this distils the part we need into a small
JSON that ships with the tools:

    python tools/build_species_map.py

Writes tools/data/species_models.json: {FileDataID: species}.
"""
from __future__ import annotations

import json
import re
import sys

from tools.config import DATA_DIR
from tools.wowdata import load_db2

LISTFILE = DATA_DIR / "verified-listfile.csv"
OUT = DATA_DIR / "species_models.json"


def main() -> int:
    if not LISTFILE.exists():
        print(f"no listfile at {LISTFILE}; fetch it from wowdev/wow-listfile releases", file=sys.stderr)
        return 1

    # FileDataID -> species, for every model under creature/<species>/
    species_by_file: dict[int, str] = {}
    pattern = re.compile(r"^(\d+);creature/([^/]+)/[^/]+\.m2$", re.I)
    with LISTFILE.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pattern.match(line.strip())
            if m:
                species_by_file[int(m.group(1))] = m.group(2).lower()
    print(f"models named by the listfile: {len(species_by_file)}")

    # keep only the ones some creature display actually uses
    used = set()
    for row in load_db2("CreatureModelData").values():
        fdid = int(row.get("FileDataID") or 0)
        if fdid in species_by_file:
            used.add(fdid)
    mapping = {str(f): species_by_file[f] for f in sorted(used)}
    print(f"of those, used by CreatureModelData: {len(mapping)}")

    OUT.write_text(json.dumps(mapping, indent=0, sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB, {len(set(mapping.values()))} distinct species)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
