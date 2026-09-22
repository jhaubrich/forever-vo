"""Text normalisation and hashing, byte-for-byte identical to Util.Tokenize /
Util.NormalizeText / Util.HashText in ForeverVO/Core/Util.lua. The addon looks
gossip lines up by this key, so the two implementations must never drift.

The client resolves $n, $c and $r against whoever is reading before any addon
sees the text, so captured text says "Myrlin" and "rogue" where the database
says $n and $c. Both sides drop all three, so a line matches whoever reads it.
"""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]")
_DOLLAR_CODE = re.compile(r"\$[a-z]")
_GENDER_CODE = re.compile(r"\$g[^;]*;")


def tokenize(
    text: str | None,
    player_name: str | None = None,
    class_name: str | None = None,
    race_name: str | None = None,
) -> str | None:
    """Mirror of Util.Tokenize: puts $n/$c/$r back where the client expanded them.

    Capitalisation is kept, so a capitalised match becomes $N/$C/$R and
    textclean reads it as a sentence-initial "Adventurer".
    """
    if not text:
        return text

    def put(subject: str, value: str | None, token: str) -> str:
        if not value:
            return subject
        # Word boundaries: see Util.Tokenize. Explicit lookarounds rather than \b,
        # because Python's \w includes "_" and Lua's %w does not.
        return re.sub(
            r"(?<![0-9A-Za-z])" + re.escape(value) + r"(?![0-9A-Za-z])",
            lambda m, t=token: t.upper() if m.group(0)[:1].isupper() else t,
            subject,
            flags=re.IGNORECASE,
        )

    text = put(text, player_name, "$n")
    first_name = player_name.split()[0] if player_name and player_name.split() else None
    if first_name and first_name != player_name:
        text = put(text, first_name, "$n")   # $n is the bare first name
    text = put(text, class_name, "$c")
    text = put(text, race_name, "$r")
    return text


def normalize(
    text: str | None,
    player_name: str | None = None,
    class_name: str | None = None,
    race_name: str | None = None,
) -> str:
    if not text:
        return ""
    text = tokenize(text, player_name, class_name, race_name)
    text = text.lower()
    text = _GENDER_CODE.sub("", text)   # $g male:female; branch
    text = _DOLLAR_CODE.sub("", text)   # $n, $c, $r, $b, ...
    # Lua strips per byte; encode to UTF-8 so multi-byte characters vanish the same way
    raw = text.encode("utf-8")
    return _NON_ALNUM.sub("", raw.decode("latin-1"))


def hash_text(normalized: str) -> str:
    """djb2 modulo 2^32 as 8 hex chars (the Lua side stays within double precision)."""
    h = 5381
    for byte in normalized.encode("latin-1"):
        h = (h * 33 + byte) % 4294967296
    return f"{h:08x}"


def text_key(
    text: str | None,
    player_name: str | None = None,
    class_name: str | None = None,
    race_name: str | None = None,
) -> str:
    return hash_text(normalize(text, player_name, class_name, race_name))


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:] or ["Gryphons, eh? Never really cared for the beasts."]:
        print(text_key(arg), arg)
