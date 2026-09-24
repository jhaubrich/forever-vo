"""Decodes "/fvo export" strings (FVO1:<base64 zlib json>) into capture-schema JSON.

Standard library only, so it also runs inside the GitHub Action that turns
capture issues into files under captures/.

    python tools/exportfile.py --out captures/issue-12.json < body.txt
    python tools/exportfile.py --out captures/mine.json "FVO1:eJy..."
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import zlib
from pathlib import Path

PREFIX = "FVO1:"
_TOKEN = re.compile(r"FVO1:[A-Za-z0-9+/=\s]+")


def find_export(text: str) -> str | None:
    """Pulls the export string out of free text (an issue body, a chat paste)."""
    match = _TOKEN.search(text)
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(0))


def decode(export: str) -> dict:
    if not export.startswith(PREFIX):
        raise ValueError("not a Forever Voiceover export string")
    raw = base64.b64decode(export[len(PREFIX):])
    data = json.loads(zlib.decompress(raw).decode("utf-8"))
    if data.get("v") != 1:
        raise ValueError(f"unsupported export version {data.get('v')}")
    return data


def to_capture(data: dict, origin: str) -> dict:
    """Converts the compact export into the capture.json schema used by the tools."""
    out = {"version": 2, "source": "community", "origin": origin, "quests": {}, "gossip": {}, "npcs": {}}
    if data.get("addon"):
        out["addon"] = data["addon"]   # ingest.py gates repairs on the addon that tokenised the text
    for line in data.get("lines", []):
        entry = {
            "event": line.get("e"),
            "questID": line.get("q"),
            "title": line.get("t"),
            "text": line.get("x"),
            "npc": line.get("n"),
            "name": line.get("s"),
            "isObject": line.get("o") or None,
            "zone": line.get("z"),
            "mapID": line.get("m"),
            "sex": line.get("g"),            # "m"/"f", from addon 0.1.4 on
            "wanted": line.get("w") or None,  # a voiced line the pack asked to hear again
            "build": data.get("build"),
            "source": "community",
        }
        if not entry["text"]:
            continue
        if line.get("k") == "quest":
            if not entry["questID"]:
                continue
            out["quests"][f"{entry['questID']}-{entry['event']}"] = entry
        else:
            from textkey import text_key  # local import keeps the stdlib-only path for --raw
            out["gossip"][f"{entry['npc'] or entry['name'] or '?'}|{text_key(entry['text'])}"] = entry
    for key, npc in (data.get("npcs") or {}).items():
        out["npcs"][str(key)] = {k: v for k, v in npc.items() if v is not None}
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", nargs="?", help="export string, or a file containing one; stdin if omitted")
    parser.add_argument("--out", required=True, help="where to write the decoded JSON")
    parser.add_argument("--origin", default="manual", help="label recorded in the file (e.g. issue-12)")
    parser.add_argument("--raw", action="store_true", help="write the decoded export as-is instead of capture schema")
    args = parser.parse_args(argv)

    if args.export and Path(args.export).exists():
        text = Path(args.export).read_text(encoding="utf-8")
    elif args.export:
        text = args.export
    else:
        text = sys.stdin.read()
    export = find_export(text)
    if not export:
        print("no FVO1: export string found", file=sys.stderr)
        return 2
    data = decode(export)
    result = data if args.raw else to_capture(data, args.origin)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    lines = len(data.get("lines", []))
    print(f"{out}: {lines} lines, {len(data.get('npcs') or {})} speakers, client build {data.get('build')}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main(sys.argv[1:]))
