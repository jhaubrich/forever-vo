"""Reads quest text out of the beta client's cache (Cache/WDB/enUS/questcache.wdb)
into tools/data/bulk/questcache.json.

The client caches the server's quest query response for every quest it has
seen listed, so this holds Forever's actual text (including revisions) for far
more quests than the player has accepted. Only the offer text is in the cache;
turn-in and progress text are not.

Record layout (build 69913): a fixed part whose size is 488 + 43*n bytes,
then bit-packed string lengths (9,12,12,9,10,8,10,8,11 bits, MSB first),
then optional quest objectives, then the strings back to back at the end.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from config import BETA_DIR, DATA_DIR

CACHE = BETA_DIR / "Cache" / "WDB" / "enUS" / "questcache.wdb"
OUTPUT = DATA_DIR / "bulk" / "questcache.json"
LENGTH_BITS = [9, 12, 12, 9, 10, 8, 10, 8, 11]
FIELDS = ["title", "objectives", "details", "area", "giverText", "giverName", "turninText", "turninName", "completionLog"]
FIXED_BASE, FIXED_STEP, FIXED_MAX_STEPS = 488, 43, 12


class Bits:
    def __init__(self, buf: bytes, byte_pos: int):
        self.buf = buf
        self.pos = byte_pos * 8

    def read(self, n: int) -> int:
        value = 0
        for _ in range(n):
            byte = self.buf[self.pos >> 3]
            value = (value << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value


def decode_at(record: bytes, p: int) -> dict[str, str] | None:
    if p + 12 > len(record):
        return None
    bits = Bits(record, p)
    lengths = [bits.read(n) for n in LENGTH_BITS]
    total = sum(lengths)
    start = len(record) - total
    if lengths[0] == 0 or start < p + 12:
        return None
    blob = record[start:]
    parts, offset = [], 0
    for n in lengths:
        try:
            text = blob[offset:offset + n].decode("utf-8")
        except UnicodeDecodeError:
            return None
        if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
            return None
        parts.append(text)
        offset += n
    title = parts[0]
    if not title[0].isalnum() and title[0] not in "\"'":
        return None
    return dict(zip(FIELDS, parts))


def parse_record(record: bytes) -> dict[str, str] | None:
    for steps in range(FIXED_MAX_STEPS):
        parsed = decode_at(record, FIXED_BASE + FIXED_STEP * steps)
        if parsed:
            return parsed
    return None


def read_cache(path: Path) -> dict[int, dict[str, str]]:
    data = path.read_bytes()
    magic, build = struct.unpack_from("<4sI", data, 0)
    if magic[::-1] != b"WQST":
        raise SystemExit(f"{path} is not a quest cache")
    pos = 24
    quests: dict[int, dict[str, str]] = {}
    failed = 0
    while pos + 8 <= len(data):
        entry, length = struct.unpack_from("<II", data, pos)
        if entry == 0 and length == 0:
            break
        record = data[pos + 8:pos + 8 + length]
        pos += 8 + length
        parsed = parse_record(record)
        if parsed:
            quests[entry] = parsed
        else:
            failed += 1
    print(f"{path.name}: build {build}, {len(quests)} quests parsed, {failed} records not understood")
    return quests


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else CACHE
    quests = read_cache(path)
    out = {"version": 2, "source": "questcache", "quests": {}, "gossip": {}, "npcs": {}}
    for quest_id, q in sorted(quests.items()):
        if q["details"].strip():
            out["quests"][f"{quest_id}-accept"] = {
                "event": "accept", "questID": quest_id, "title": q["title"], "text": q["details"],
                "source": "questcache",
            }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"{OUTPUT}: {len(out['quests'])} quest offer texts")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
