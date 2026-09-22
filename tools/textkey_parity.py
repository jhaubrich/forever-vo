"""Proves tools/textkey.py and ForeverVO/Core/Util.lua hash text identically.

    ./tools/run.sh tools/textkey_parity.py

The addon looks gossip lines up by this key, so a drift between the two
implementations silently stops every gossip line from matching. CLAUDE.md
requires re-running this after touching either side.

The corpus is the real captured text, the tokenised beta quest cache, and a set
of edge cases (multi-byte characters, $G branches, capitalisation, empty text).
Needs lua 5.1 on PATH; on NixOS: nix shell nixpkgs#lua5_1 -c ./tools/run.sh tools/textkey_parity.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textkey import text_key, tokenize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "tools" / "data" / "capture.json"
QUESTCACHE = ROOT / "tools" / "data" / "bulk" / "questcache.json"
UTIL_LUA = ROOT / "ForeverVO" / "Core" / "Util.lua"

EDGE_CASES = [
    "$Ghe:she; said $N, the $c.",
    "café naïve — em dash",
    "ALL CAPS ROGUE MYRLIN",
    "no tokens at all",
    "",
    "Mixed $B$B newlines\r\nhere",
    "Rogue at the start",
    "trailing $c",
]

# Regression guard: a short character name is a substring of ordinary English.
# Without word boundaries in Util.Tokenize a player called "It" turned "with"
# into "w$nh", which the pipeline then voiced as "wadventurerh". Parity alone
# would not catch this -- both sides were wrong identically -- so the text is
# asserted unchanged here and the same rows go through the Lua comparison.
SUBSTRING_CASES = [
    ("Must have been quite a shock, with these items.", "It", "Paladin", "Undead"),
    ("Recruits exploit the situation and sit down.", "It", "Paladin", "Undead"),
    ("The Dalaran magi guard the council chamber.", "Mag", "Mage", "Human"),
]


def check_substrings() -> int:
    """A name may only tokenise as a whole word. Returns the number of failures."""
    failures = 0
    for text, player, class_name, race in SUBSTRING_CASES:
        got = tokenize(text, player, class_name, race)
        if got != text:
            print(f"substring guard FAILED for player {player!r}:\n  {text!r}\n  {got!r}")
            failures += 1
    return failures


HARNESS = """
format = string.format
local stubName, stubClass, stubRace
function UnitName() return stubName end
function UnitClass() return stubClass end
function UnitRace() return stubRace end
local ns = {}
assert(loadfile(arg[1]))("ForeverVO", ns)
for _, row in ipairs(assert(loadfile(arg[2]))()) do
    stubName, stubClass, stubRace = row.player, row.class, row.race
    print(ns.Util.TextKey(row.text, row.player, row.class, row.race))
end
"""


def rows() -> list[dict]:
    out = []
    if CAPTURE.exists():
        data = json.loads(CAPTURE.read_text(encoding="utf-8"))
        for section in ("quests", "gossip"):
            for entry in data.get(section, {}).values():
                if entry.get("text"):
                    out.append({k: entry.get(k) for k in ("text", "player", "class", "race")})
    if QUESTCACHE.exists():
        data = json.loads(QUESTCACHE.read_text(encoding="utf-8"))
        for entry in data.get("quests", {}).values():
            if entry.get("text"):
                out.append({"text": entry["text"], "player": None, "class": None, "race": None})
    for text in EDGE_CASES:
        out.append({"text": text, "player": "Myrlin", "class": "Rogue", "race": "Human"})
    for text, player, class_name, race in SUBSTRING_CASES:
        out.append({"text": text, "player": player, "class": class_name, "race": race})
    return out


def lua_string(value: str | None) -> str:
    if value is None:
        return "nil"
    chars = []
    for char in value:
        if char in '\\"':
            chars.append("\\" + char)
        elif char == "\n":
            chars.append("\\n")
        elif char == "\r":
            chars.append("\\r")
        elif ord(char) < 32 or ord(char) > 126:
            chars.extend("\\%d" % byte for byte in char.encode("utf-8"))
        else:
            chars.append(char)
    return '"' + "".join(chars) + '"'


def main() -> int:
    if not shutil.which("lua"):
        print("lua 5.1 is not on PATH (nix shell nixpkgs#lua5_1 -c ...)", file=sys.stderr)
        return 2
    if check_substrings():
        return 1
    corpus = rows()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "corpus.lua").write_text(
            "return {\n"
            + "".join(
                "  {text=%s, player=%s, class=%s, race=%s},\n"
                % (lua_string(r["text"]), lua_string(r["player"]), lua_string(r["class"]), lua_string(r["race"]))
                for r in corpus
            )
            + "}\n",
            encoding="utf-8",
        )
        (tmp / "parity.lua").write_text(HARNESS, encoding="utf-8")
        result = subprocess.run(
            ["lua", str(tmp / "parity.lua"), str(UTIL_LUA), str(tmp / "corpus.lua")],
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return 2
    actual = result.stdout.splitlines()
    expected = [text_key(r["text"], r["player"], r["class"], r["race"]) for r in corpus]
    if len(actual) != len(expected):
        print(f"lua produced {len(actual)} keys, python {len(expected)}", file=sys.stderr)
        return 1
    bad = [(i, e, a) for i, (e, a) in enumerate(zip(expected, actual)) for _ in (0,) if e != a]
    for index, want, got in bad[:10]:
        print(f"row {index}: python {want} != lua {got}\n    {corpus[index]['text'][:120]!r}", file=sys.stderr)
    if bad:
        print(f"{len(bad)} of {len(expected)} keys differ", file=sys.stderr)
        return 1
    print(f"parity OK: {len(expected)} keys identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
