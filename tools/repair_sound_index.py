# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""Puts the voice back on sound_index.json entries that lost it.

Before 2026-09-22 two bulk workers erased each other's index records: each
one's table rebuild probed the other's fresh files and wrote placeholders
({"d": ..., "v": null}) over the real entries. The voice each file was
generated in is still in the journal, one line per file:

    [2089/6606] Gossip/5688-19cbe7de.mp3  20.6s audio in 18.3s  [scourge-female] Innkeeper Renee

    ./tools/run.sh tools/repair_sound_index.py            # last 14 days of journal
    ./tools/run.sh tools/repair_sound_index.py --since "30 days ago" --dry-run

Only entries with no voice whose file exists are touched, and the last line
for a file wins (tools/data/*.log from older runs are read as well). Text
fingerprints are not logged; run `generate.py --reindex` afterwards to stamp
the current text on them. Entries with no line anywhere keep no voice, which
`wanted()` treats as "do not regenerate": the voice-change check stays blind
for those files.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate  # noqa: E402
from config import DATA_DIR, SOUND_INDEX, SOUNDS_DIR  # noqa: E402

_LINE = re.compile(r"\[\d+/\d+\] (\S+)\.mp3\s+[\d.]+s audio in\s+[\d.]+s\s+\[([^\]]+)\]")
UNITS = ("forever-vo-bulk", "forever-vo-daily")


def journal_voices(since: str) -> dict[str, str]:
    """index key -> voice, from every generated-file line in the units' journals
    and in the gitignored tools/data/*.log files older runs wrote (the logs are
    read first, so a newer journal line wins)."""
    voices: dict[str, str] = {}
    texts = [path.read_text(encoding="utf-8", errors="replace") for path in sorted(DATA_DIR.glob("*.log"))]
    for unit in UNITS:
        texts.append(subprocess.run(
            ["journalctl", "--user", "-u", unit, "--since", since, "--no-pager", "-o", "cat"],
            capture_output=True, text=True,
        ).stdout)
    for out in texts:
        for match in _LINE.finditer(out):
            parts = match.group(1).split("/")
            if len(parts) == 4 and parts[1] == "Narrator":
                key = generate.index_key(parts[3], parts[2])
            else:
                key = parts[-1]
            voices[key] = match.group(2)
    return voices


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="14 days ago", help="journalctl --since (default: 14 days ago)")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)

    import json
    index = json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
    voices = journal_voices(args.since)
    repaired: dict[str, dict] = {}
    unresolved: list[str] = []
    for key, value in index.items():
        if generate.index_rank(value) != 0 or not isinstance(value, dict):
            continue
        voice = voices.get(key)
        if voice is None:
            unresolved.append(key)
            continue
        if key.startswith("Narrator/"):
            _, alt_voice, base = key.split("/", 2)
            path = generate.sound_path(generate.sound_folder(base), base, alt_voice)
        else:
            path = SOUNDS_DIR / generate.sound_folder(key) / f"{key}.mp3"
        if not path.exists():
            unresolved.append(key)
            continue
        repaired[key] = {**value, "v": voice}
    print(f"{len(voices)} generated files in the journal; {len(repaired)} placeholder entries get their voice back, "
          f"{len(unresolved)} placeholders have no journal line or no file")
    if unresolved[:10]:
        print("  unresolved e.g.:", ", ".join(unresolved[:10]))
    if args.dry_run or not repaired:
        return 0
    generate.save_sound_index(repaired, set(repaired))
    print(f"wrote {len(repaired)} entries to {SOUND_INDEX}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
