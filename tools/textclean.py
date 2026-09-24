"""Turns raw WoW quest/gossip text into something a TTS model should read aloud.

Mirrors the rules of the upstream tts_cli (dollar-code substitution, stage
directions in angle brackets, $G gender branches) and adds sentence chunking for
models that prefer short inputs.
"""
from __future__ import annotations

import re

# Same substitutions as upstream tts_cli/tts_utils.py REPLACE_DICT.
#
# $c (the player's class) renders as "adventurer" like $n, which has two known
# faults: a line carrying both says "adventurer" twice, and "adventurer" is
# vowel-initial, so the 74 lines written "a $c" ("I cannot train a $c such as
# yourself") come out "a adventurer". "friend" fixes both -- consonant-initial,
# what Classic NPCs actually call you, and correct in every position the corpus
# uses -- at the cost of 2 possessive lines ("your first friend's robes").
#
# Deliberately NOT changed yet: 1,246 lines carry $c and 318 of them already
# have audio, so swapping the word costs ~1.1 h of GPU that the first full
# generation needs more. The fingerprints are seeded (generate.py --reindex), so
# whenever this changes, `generate.py --stale-only` finds exactly the affected
# files by itself. Revisit once the bulk backlog is done.
#
# One word for everyone either way: the audio is rendered once, so a per-player
# choice would mean a full extra copy of every $c line (~1,270 files, ~4.6 h)
# per option, and the word cannot be spliced in at runtime -- the client only
# has PlaySoundFile, and the clip would need to exist in each of the ~38 cloned
# voices to match the line around it.
REPLACE = {
    "$b": "\n", "$B": "\n",
    "$n": "adventurer", "$N": "Adventurer",
    "$c": "adventurer", "$C": "Adventurer",
    "$r": "traveler", "$R": "Traveler",
}

# Respellings for names Chatterbox gets wrong. The model takes plain text only
# (no IPA or SSML), so the fix is spelling the word the way it should sound.
# Whole words, any case; a shouted all-caps word stays all caps. Changing an
# entry changes the spoken-text fingerprint, so `generate.py --stale-only`
# regenerates exactly the affected files. Try candidates by ear first: of five
# for Gnomeregan (/noʊmɹəˈgɑːn/), "Nomer-gahn" was natural, "Gnome-ruh-gahn"
# was bad, and "Nome-ruh-GAHN" came out partly as numbers (so do not mark stress
# with capitals). Ahn'Qiraj is fine as written; "Ahn Kih-rahj" adds a rolled r.
# Quel'Thalas came out wrong; "Quell Thalas" and "Kwel-thalas" were both fine.
# Kharanos as written varies take to take, and so do "Karanos" and "Karra-noss"
# (each had bad takes out of four); "Karranos" was fine all four times, sometimes
# with a Scottish rolled r. Dun Morogh is fine as written; one bad take in game
# ("Muro-h") was a bad sample, fixed by regenerating the file. Tirisfal as
# written sometimes came out "Tirefal"; "Tirrisfal" and "Teerisfall" were right
# four times of four. Hyphens can leave a pause mid-word ("Teer-iss-fall",
# "Tirriss-fall"), so prefer respellings without them. Varimathras as written
# was only ever ok or good; "Vairimathrus" was good four times of four.
# Sharptalon reads best as two words; "Sharp Talon" beat the plain spelling and
# "Sharptallon", so a space does not leave the pause a hyphen can.
PRONUNCIATIONS = {
    "Ahn'Qiraj": "Ahn Kih-rahj",
    "Gnomeregan": "Nomer-gahn",
    "Kharanos": "Karranos",
    "Quel'Thalas": "Quell Thalas",
    "Sharptalon": "Sharp Talon",
    "Tirisfal": "Tirrisfal",
    "Varimathras": "Vairimathrus",
}
_PRONUNCIATION = re.compile(r"\b(" + "|".join(map(re.escape, PRONUNCIATIONS)) + r")\b", re.IGNORECASE)
_PRONUNCIATION_KEYS = {word.lower(): spoken for word, spoken in PRONUNCIATIONS.items()}


def _respell(match: re.Match) -> str:
    spoken = _PRONUNCIATION_KEYS[match.group(1).lower()]
    return spoken.upper() if match.group(1).isupper() else spoken


_GENDER = re.compile(r"\$[Gg]\s*([^:;]+?)\s*:\s*([^:;]+?)\s*;")
_STAGE_DIRECTION = re.compile(r"<[^<>]*>\s?")
_STAGE_DIRECTION_TEXT = re.compile(r"<([^<>]*)>")
_STAGE_SPLIT = re.compile(r"(<[^<>]*>)")
_WHITESPACE = re.compile(r"\s+")


def has_gender_branch(text: str) -> bool:
    return bool(_GENDER.search(text))


def split_gender(text: str) -> tuple[str, str]:
    """Returns (male_text, female_text) for `$G he:she;` style branches."""
    return _GENDER.sub(r"\1", text), _GENDER.sub(r"\2", text)


def _substitute(text: str) -> str:
    for key, value in REPLACE.items():
        text = text.replace(key, value)
    return text


def _finish(text: str) -> str:
    text = _PRONUNCIATION.sub(_respell, text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def clean(text: str, keep_stage_directions: bool = False) -> str:
    """The whole line as one reader says it. Stage directions (<the guard spits>)
    are the narrator's, not the speaker's, so they are dropped -- unless the
    narrator reads the whole line anyway, when their text is kept as prose."""
    text = _substitute(text)
    if keep_stage_directions:
        text = _STAGE_DIRECTION_TEXT.sub(r"\1", text)
    else:
        text = _STAGE_DIRECTION.sub("", text)
    return _finish(text)


def segments(text: str) -> list[tuple[str, str]]:
    """The line in reading order as ("npc", words) and ("narrator", words) pieces,
    each cleaned like clean(): the speaker's own words and, between them, every
    <stage direction> for the narrator. Adjacent pieces of one role are merged.
    A line with no stage direction is a single npc piece."""
    out: list[tuple[str, str]] = []
    for piece in _STAGE_SPLIT.split(_substitute(text)):
        if piece.startswith("<") and piece.endswith(">"):
            role, piece = "narrator", piece[1:-1]
        else:
            role = "npc"
        piece = _finish(piece)
        if not piece:
            continue
        if out and out[-1][0] == role:
            out[-1] = (role, f"{out[-1][1]} {piece}")
        else:
            out.append((role, piece))
    return out


def is_speakable(text: str) -> bool:
    """False when unresolved markup remains ($ codes, angle brackets) or nothing is left."""
    return bool(text) and "$" not in text and "<" not in text and ">" not in text


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"'(A-Z0-9])")


def chunk(text: str, max_chars: int = 300) -> list[str]:
    """Splits text into sentence-aligned chunks no longer than max_chars where possible."""
    sentences = _SENTENCE_END.split(text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            # Fall back to splitting on commas/semicolons for run-on sentences
            parts = re.split(r"(?<=[,;:])\s+", sentence)
            for part in parts:
                if current and len(current) + 1 + len(part) > max_chars:
                    chunks.append(current)
                    current = part
                else:
                    current = f"{current} {part}".strip()
            continue
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


# Key normalisation used by the addon's lookup tables (DataModules.lua replaces
# double quotes with single quotes before matching, generators strip newlines).
def lookup_key(text: str) -> str:
    return text.replace('"', "'").replace("\r", " ").replace("\n", " ")


def first_n_words(text: str, n: int) -> str:
    return " ".join(re.findall(r"\S+", text)[:n])


def last_n_words(text: str, n: int) -> str:
    return " ".join(re.findall(r"\S+", text)[-n:])


def quest_text_excerpt(text: str) -> str:
    """The addon fuzzy-matches on the first and last 15 words of the quest text."""
    excerpt = first_n_words(text, 15) + " " + last_n_words(text, 15)
    excerpt = re.sub(r"(\$[Bb])+", " ", lookup_key(excerpt))
    return excerpt
