"""Merges ForeverVOCaptureDB saved-variable files into tools/data/capture.json.

The Forever beta client writes SavedVariables on logout but never reads them
back, so each session's file only holds that session. Run this after each play
session to accumulate everything the addon has seen.

    ./tools/run.sh tools/ingest.py            # scans the beta WTF folder
    ./tools/run.sh tools/ingest.py file.lua   # or explicit files
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from config import ADDON_NAME, BETA_DIR, CAPTURE_JSON, LEGACY_CHARACTERS, ROOT
from luatable import parse_saved_variables
from textkey import text_key, tokenize

CAPTURES_DIR = ROOT / "captures"   # community exports decoded by tools/exportfile.py
SOURCES_JSON = CAPTURE_JSON.with_name("capture.sources.json")  # mtime bookkeeping, not versioned

SV_NAME = f"{ADDON_NAME}.lua"
CAPTURE_VAR = "ForeverVOCaptureDB"



def character_traits(entry: dict) -> tuple[str | None, str | None, str | None]:
    """The reader's name, class and race, falling back to LEGACY_CHARACTERS for
    captures made before the addon recorded them (capture version 3)."""
    player = entry.get("player") or None
    legacy = LEGACY_CHARACTERS.get(player or "", {})
    return player, entry.get("class") or legacy.get("class"), entry.get("race") or legacy.get("race")


def tokenize_entry(entry: dict) -> dict:
    """Puts $n/$c/$r back where the client expanded them. Without this a line
    first seen on a rogue is voiced as "rogue" for every class that hears it."""
    text = entry.get("text")
    if not text:
        return entry
    fixed = tokenize(text, *character_traits(entry))
    if fixed == text:
        return entry
    entry = dict(entry)
    entry["text"] = fixed
    return entry


def gossip_key(key: str, entry: dict) -> str:
    """<speaker>|<text hash>, recomputed so a re-tokenised line keys the same way
    the addon will look it up (ForeverVO/Core/Util.lua Util.TextKey)."""
    speaker = str(key).split("|", 1)[0]
    return f"{speaker}|{text_key(entry.get('text'), *character_traits(entry))}"

def find_saved_variable_files() -> list[Path]:
    files: list[Path] = []
    for path in (BETA_DIR / "WTF" / "Account").glob(f"*/SavedVariables/{SV_NAME}*"):
        if path.suffix in (".lua", ".bak"):
            files.append(path)
    return sorted(files)


def load_capture() -> dict:
    capture = json.loads(CAPTURE_JSON.read_text(encoding="utf-8")) if CAPTURE_JSON.exists() else {"version": 2, "quests": {}, "gossip": {}, "npcs": {}}
    capture.pop("sources", None)  # older files kept it inline
    capture["sources"] = json.loads(SOURCES_JSON.read_text(encoding="utf-8")) if SOURCES_JSON.exists() else {}
    return capture


def merge_entry(store: dict, key: str, entry: dict) -> bool:
    old = store.get(key)
    if old is None:
        store[key] = entry
        return True
    if (entry.get("time") or 0) >= (old.get("time") or 0):
        entry = dict(entry)
        entry["firstSeen"] = old.get("firstSeen", old.get("time"))
        changed = entry != old
        store[key] = entry
        return changed
    return False


def ingest_file(capture: dict, path: Path) -> tuple[int, int, int]:
    if path.suffix == ".json":
        db = json.loads(path.read_text(encoding="utf-8"))
    else:
        variables = parse_saved_variables(path.read_text(encoding="utf-8", errors="replace"))
        db = variables.get(CAPTURE_VAR)
    if not isinstance(db, dict):
        return (0, 0, 0)
    quests = gossip = npcs = 0
    for key, entry in (db.get("quests") or {}).items():
        quests += merge_entry(capture["quests"], str(key), tokenize_entry(entry))
    for key, entry in (db.get("gossip") or {}).items():
        entry = tokenize_entry(entry)
        gossip += merge_entry(capture["gossip"], gossip_key(key, entry), entry)
    for key, npc in (db.get("npcs") or {}).items():
        old = capture["npcs"].get(str(key), {})
        merged = {**old, **{k: v for k, v in npc.items() if v is not None}}
        if merged != old:
            npcs += 1
        capture["npcs"][str(key)] = merged
    stat = path.stat()
    capture["sources"][str(path)] = {"mtime": stat.st_mtime, "size": stat.st_size}
    return quests, gossip, npcs


def backfill(capture: dict) -> tuple[int, int]:
    """Re-tokenises text already in capture.json and re-keys any gossip line whose
    hash moves as a result. Idempotent, so it just runs on every ingest: it is
    what repairs everything captured before the addon recorded class and race."""
    quests = 0
    for key, entry in capture["quests"].items():
        fixed = tokenize_entry(entry)
        if fixed is not entry:
            capture["quests"][key] = fixed
            quests += 1
    gossip, rebuilt = 0, {}
    for key, entry in capture["gossip"].items():
        fixed = tokenize_entry(entry)
        new_key = gossip_key(key, fixed)
        if fixed is not entry or new_key != key:
            gossip += 1
        rebuilt[new_key] = fixed
    capture["gossip"] = rebuilt
    return quests, gossip


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or (find_saved_variable_files() + sorted(CAPTURES_DIR.glob("*.json")))
    if not files:
        print(f"no {SV_NAME} files found under {BETA_DIR / 'WTF' / 'Account'}")
        return 1
    capture = load_capture()
    for path in files:
        seen = capture["sources"].get(str(path))
        stat = path.stat()
        if seen and seen["mtime"] == stat.st_mtime and seen["size"] == stat.st_size:
            print(f"unchanged  {path}")
            continue
        quests, gossip, npcs = ingest_file(capture, path)
        print(f"ingested   {path}: {quests} quest, {gossip} gossip, {npcs} npc changes")

    retokenised = backfill(capture)
    if any(retokenised):
        print(f"re-tokenised  {retokenised[0]} quest, {retokenised[1]} gossip texts ($n/$c/$r put back)")

    CAPTURE_JSON.parent.mkdir(parents=True, exist_ok=True)
    sources = capture.pop("sources")
    CAPTURE_JSON.write_text(json.dumps(capture, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    SOURCES_JSON.write_text(json.dumps(sources, indent=1, sort_keys=True), encoding="utf-8")
    missing_q = sum(1 for e in capture["quests"].values() if not e.get("found"))
    missing_g = sum(1 for e in capture["gossip"].values() if not e.get("found"))
    print(f"capture.json: {len(capture['quests'])} quest texts ({missing_q} without audio), "
          f"{len(capture['gossip'])} gossip texts ({missing_g} without audio), {len(capture['npcs'])} NPCs")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
