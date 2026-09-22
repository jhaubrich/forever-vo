"""Decodes "/fvo export" strings (FVO1:<base64 zlib json>) into capture-schema JSON,
and builds them back out of capture.json so already-voiced lines can still be shared.

Standard library only, so it also runs inside the GitHub Action that turns
capture issues into files under captures/.

    python tools/exportfile.py --out captures/issue-12.json < body.txt
    python tools/exportfile.py --out captures/mine.json "FVO1:eJy..."
    python tools/exportfile.py --encode --since 2026-09-22 --out /tmp/share.txt
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
# Stops before a following export string. ":" is not in the base64 alphabet, so
# "FVO1:" can only ever be a prefix - without the lookahead a body holding two
# chunks matches the first plus the next one's "FVO1", which decodes to garbage.
_TOKEN = re.compile(r"FVO1:(?:(?!FVO1:)[A-Za-z0-9+/=\s])+")


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


# ---------------------------------------------------------------------------
# The other direction: build an export out of capture.json
# ---------------------------------------------------------------------------

# Exactly the fields Export:Collect sends. The character's name, class and race
# are not among them, so nothing identifying leaves this file either.
NPC_FIELDS = ("name", "sex", "displayID", "modelFileID", "creatureType", "isObject")


def from_capture(capture: dict, since: float = 0.0, addon: str = "", build: str = "") -> dict:
    """Builds an FVO1 payload from lines the player saw in game.

    "/fvo export" can only offer lines nothing can voice yet - Export:Collect skips
    entry.found - so once a pack covers a line the game stops offering it, and a
    locally generated pack quietly empties the contribution. Everything the client
    captured is still in capture.json, so this builds the same payload from there.
    """
    lines: list[dict] = []
    used: set[str] = set()
    for kind, section in (("quest", "quests"), ("gossip", "gossip")):
        for entry in capture.get(section, {}).values():
            if not entry.get("player") or not entry.get("text"):
                continue
            if (entry.get("time") or 0) < since:
                continue
            lines.append({"k": kind, "e": entry.get("event"), "q": entry.get("questID"),
                          "t": entry.get("title"), "x": entry["text"], "n": entry.get("npc"),
                          "s": entry.get("name"), "o": entry.get("isObject"),
                          "z": entry.get("zone"), "m": entry.get("mapID")})
            if entry.get("npc"):
                used.add(str(entry["npc"]))
    npcs = {key: {f: npc[f] for f in NPC_FIELDS if npc.get(f) is not None}
            for key, npc in (capture.get("npcs") or {}).items() if key in used}
    return {"v": 1, "addon": addon, "build": build, "lines": lines, "npcs": npcs}


def encode(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return PREFIX + base64.b64encode(zlib.compress(raw, 9)).decode("ascii")


def encode_chunks(data: dict, max_chars: int) -> list[str]:
    """Splits into strings that each fit in one GitHub comment; find_export only ever
    picks the first match in a body, so every chunk has to be its own comment."""
    envelope = {k: v for k, v in data.items() if k not in ("lines", "npcs")}
    npcs = data.get("npcs") or {}

    def build(batch: list[dict]) -> dict:
        used = {str(line["n"]) for line in batch if line.get("n")}
        return {**envelope, "lines": batch,
                "npcs": {k: v for k, v in npcs.items() if k in used}}

    chunks: list[str] = []
    batch: list[dict] = []
    for line in data["lines"]:
        batch.append(line)
        if len(encode(build(batch))) > max_chars:
            if len(batch) == 1:
                raise ValueError("a single line does not fit in --max-chars")
            batch.pop()
            chunks.append(encode(build(batch)))
            batch = [line]
    if batch:
        chunks.append(encode(build(batch)))
    return chunks


def run_encode(args) -> int:
    import datetime
    capture = json.loads(Path(args.capture).read_text(encoding="utf-8"))
    since = 0.0
    if args.since:
        since = datetime.datetime.strptime(args.since, "%Y-%m-%d").timestamp()
    toc = Path("ForeverVO/ForeverVO.toc")
    addon = ""
    if toc.exists():
        for line in toc.read_text(encoding="utf-8").splitlines():
            if line.startswith("## Version:"):
                addon = line.split(":", 1)[1].strip()
    builds = {e.get("build") for section in ("quests", "gossip")
              for e in capture.get(section, {}).values() if e.get("build")}
    data = from_capture(capture, since, addon, max(builds, default="") )
    if not data["lines"]:
        print("no player-captured lines to share", file=sys.stderr)
        return 1
    chunks = encode_chunks(data, args.max_chars)
    if args.chunk:
        if not 1 <= args.chunk <= len(chunks):
            print(f"--chunk {args.chunk}: there are {len(chunks)}", file=sys.stderr)
            return 2
        chunks = [chunks[args.chunk - 1]]
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        paths = [out] if len(chunks) == 1 else [
            out.with_name(f"{out.stem}-{i}{out.suffix}") for i in range(1, len(chunks) + 1)]
        for path, chunk in zip(paths, chunks):
            path.write_text(chunk + "\n", encoding="utf-8")
        where = paths[0].name if len(paths) == 1 else f"{paths[0].name} .. {paths[-1].name}"
        print(f"{len(data['lines'])} lines, {len(data['npcs'])} speakers -> {where}")
        if len(chunks) > 1:
            print("paste each file as its OWN comment: the decoder only reads the first "
                  "FVO1: string in a body")
    else:
        print("\n\n".join(chunks))
        if len(chunks) > 1:
            print(f"{len(data['lines'])} lines in {len(chunks)} strings, blank line between "
                  "them; paste each as its own comment", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", nargs="?", help="export string, or a file containing one; stdin if omitted")
    parser.add_argument("--out", help="where to write the decoded JSON (or the export string with --encode)")
    parser.add_argument("--origin", default="manual", help="label recorded in the file (e.g. issue-12)")
    parser.add_argument("--raw", action="store_true", help="write the decoded export as-is instead of capture schema")
    parser.add_argument("--encode", action="store_true",
                        help="build an export string out of capture.json instead of decoding one")
    parser.add_argument("--capture", default="tools/data/capture.json", help="with --encode: the capture file to read")
    parser.add_argument("--since", help="with --encode: only lines captured on or after this date (YYYY-MM-DD)")
    parser.add_argument("--max-chars", type=int, default=60000,
                        help="with --encode: split so each string fits one GitHub comment (default 60000)")
    parser.add_argument("--chunk", type=int, metavar="N",
                        help="with --encode: print only the Nth string (1-based), for piping one at a time")
    args = parser.parse_args(argv)

    if args.encode:
        return run_encode(args)
    if not args.out:
        parser.error("--out is required when decoding")

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
