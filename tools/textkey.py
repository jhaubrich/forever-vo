"""Text normalisation and hashing, byte-for-byte identical to Util.NormalizeText /
Util.HashText in ForeverVO/Core/Util.lua. The addon looks gossip lines up by
this key, so the two implementations must never drift.
"""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]")
_DOLLAR_CODE = re.compile(r"\$[a-z]")
_GENDER_CODE = re.compile(r"\$g[^;]*;")


def normalize(text: str | None, player_name: str | None = None) -> str:
    if not text:
        return ""
    text = text.lower()
    if player_name:
        text = text.replace(player_name.lower(), "", 1)
    else:
        # Database text still holds server placeholders ($n, $b, $g he:she;, ...);
        # live text has them substituted, so drop them before hashing.
        text = _GENDER_CODE.sub("", text)
        text = _DOLLAR_CODE.sub("", text)
    # Lua strips per byte; encode to UTF-8 so multi-byte characters vanish the same way
    raw = text.encode("utf-8")
    return _NON_ALNUM.sub("", raw.decode("latin-1"))


def hash_text(normalized: str) -> str:
    """djb2 modulo 2^32 as 8 hex chars (the Lua side stays within double precision)."""
    h = 5381
    for byte in normalized.encode("latin-1"):
        h = (h * 33 + byte) % 4294967296
    return f"{h:08x}"


def text_key(text: str | None, player_name: str | None = None) -> str:
    return hash_text(normalize(text, player_name))


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:] or ["Gryphons, eh? Never really cared for the beasts."]:
        print(text_key(arg), arg)
