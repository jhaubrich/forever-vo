"""Proves tools/textkey.py and ForeverVO/Core/Util.lua hash text identically.

    ./tools/run.sh tools/textkey_parity.py

The addon looks gossip lines up by this key, so a drift between the two
implementations silently stops every gossip line from matching. CLAUDE.md
requires re-running this after touching either side.

The corpus is the real captured text, the tokenised beta quest cache, and a set
of edge cases (multi-byte characters, $G branches, capitalisation, empty text).
Needs lua 5.1 on PATH; the dev shell from flake.nix (what run.sh uses) has it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.textkey import hash_text, text_key, tokenize

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

# A non-ASCII name must redact on both sides. %f[%w] could not fire next to a
# multi-byte character, so Lua left the name in the text while Python replaced it
# -- a divergence the corpus could not show, because every captured player name
# is ASCII and the one non-ASCII edge case above is non-ASCII *text* read by
# "Myrlin". These rows go through the key comparison and the tokenize comparison.
NON_ASCII_CASES = [
    ("Hello Osel and osel today.".replace("Osel", "\u00d6sel").replace("osel", "\u00f6sel"),
     "\u00d6sel", "Mage", "Human"),
    ("Greetings, Zoe. The mage guild awaits.".replace("Zoe", "Zo\u00eb"), "Zo\u00eb", "Mage", "Human"),
    ("Senor, a word.".replace("Senor", "Se\u00f1or"), "Se\u00f1or", "Rogue", "Human"),
    ("Elodie, hello.".replace("Elodie", "\u00c9lodie"), "\u00c9lodie", "Priest", "Human"),
]

# The name matches case-sensitively, class and race do not. The client always
# renders a character name capitalised, so a lowercase "it" read by "It" is the
# word, never the name; "rogue" and "Rogue" are both the class. Each row is
# asserted against its expected output in Python and goes through the Lua
# comparison, so both sides have to agree on the case rule.
CASE_CASES = [
    ("It is done. Bring it to It, and it will be well.", "It", "Paladin", "Undead",
     "$N is done. Bring it to $N, and it will be well."),
    ("myrlin is not Myrlin, and MYRLIN is neither.", "Myrlin Fixpoint", "Rogue", "Human",
     "myrlin is not $N, and MYRLIN is neither."),
    ("A rogue, a Rogue and a ROGUE walk in; a human and a Human follow.", "Myrlin", "Rogue", "Human",
     "A $c, a $C and a $C walk in; a $r and a $R follow."),
    # A multi-word race renders $r as its last word alone (Zamja: "help you, skyborne?")
    ("Can Zamja help you, skyborne? The Windshaper Skyborne are welcome; windshapers too.",
     "Pellinore", "Hunter", "Windshaper Skyborne",
     "Can Zamja help you, $r? The $R are welcome; windshapers too."),
]


def check_substrings() -> int:
    """A name may only tokenise as a whole word, and only in its own case.
    Returns the number of failures."""
    failures = 0
    for text, player, class_name, race in SUBSTRING_CASES:
        got = tokenize(text, player, class_name, race)
        if got != text:
            print(f"substring guard FAILED for player {player!r}:\n  {text!r}\n  {got!r}")
            failures += 1
    for text, player, class_name, race, want in CASE_CASES:
        got = tokenize(text, player, class_name, race)
        if got != want:
            print(f"case guard FAILED for player {player!r}:\n  {text!r}\n  want {want!r}\n  got  {got!r}")
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
    local tokenized = ns.Util.Tokenize(row.text, row.player, row.class, row.race) or ""
    print(ns.Util.TextKey(row.text, row.player, row.class, row.race) .. ":" .. ns.Util.HashText(tokenized))
end
"""



def tokenized_hash(text, player, class_name, race) -> str:
    """djb2 over the tokenize output as bytes, so the comparison sees the text
    itself and not just the key. NormalizeText lowercases before stripping $x, so
    a $N/$n split is invisible in the key alone. Lua works on UTF-8 bytes, hence
    the latin-1 round trip."""
    out = tokenize(text, player, class_name, race) or ""
    return hash_text(out.encode("utf-8").decode("latin-1"))


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
    for text, player, class_name, race in SUBSTRING_CASES + NON_ASCII_CASES:
        out.append({"text": text, "player": player, "class": class_name, "race": race})
    for text, player, class_name, race, _ in CASE_CASES:
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
            chars.extend(f"\\{byte}" for byte in char.encode("utf-8"))
        else:
            chars.append(char)
    return '"' + "".join(chars) + '"'


def main() -> int:
    lua = shutil.which("lua5.1") or shutil.which("lua")
    if not lua:
        print("lua 5.1 is not on PATH (run through ./tools/run.sh, whose dev shell has it)", file=sys.stderr)
        return 2
    if check_substrings():
        return 1
    corpus = rows()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "corpus.lua").write_text(
            "return {\n"
            + "".join(
                f"  {{text={lua_string(r['text'])}, player={lua_string(r['player'])}, "
                f"class={lua_string(r['class'])}, race={lua_string(r['race'])}}},\n"
                for r in corpus
            )
            + "}\n",
            encoding="utf-8",
        )
        (tmp / "parity.lua").write_text(HARNESS, encoding="utf-8")
        result = subprocess.run(
            [lua, str(tmp / "parity.lua"), str(UTIL_LUA), str(tmp / "corpus.lua")],
            capture_output=True, text=True, check=False,   # the return code is reported just below
        )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return 2
    actual = result.stdout.splitlines()
    expected = [
        text_key(r["text"], r["player"], r["class"], r["race"])
        + ":" + tokenized_hash(r["text"], r["player"], r["class"], r["race"])
        for r in corpus
    ]
    if len(actual) != len(expected):
        print(f"lua produced {len(actual)} keys, python {len(expected)}", file=sys.stderr)
        return 1
    bad = [(i, e, a) for i, (e, a) in enumerate(zip(expected, actual)) for _ in (0,) if e != a]
    for index, want, got in bad[:10]:
        print(f"row {index}: python {want} != lua {got}\n    {corpus[index]['text'][:120]!r}", file=sys.stderr)
    if bad:
        print(f"{len(bad)} of {len(expected)} rows differ", file=sys.stderr)
        return 1
    print(f"parity OK: {len(expected)} keys and tokenize outputs identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
