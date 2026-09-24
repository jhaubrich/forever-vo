"""Merges ForeverVOCaptureDB saved-variable files into tools/data/capture.json.

The Forever beta client writes SavedVariables on logout but never reads them
back, so each session's file only holds that session. Run this after each play
session to accumulate everything the addon has seen.

    ./tools/run.sh tools/ingest.py            # scans the beta WTF folder
    ./tools/run.sh tools/ingest.py file.lua   # or explicit files
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

from tools.config import ADDON_NAME, BETA_DIR, CAPTURE_JSON, COMMUNITY_CHARACTERS, DATA_DIR, LEGACY_CHARACTERS, ROOT
from tools.luatable import parse_saved_variables
from tools.textclean import has_gender_branch, split_gender
from tools.textkey import text_key, tokenize

CAPTURES_DIR = ROOT / "captures"   # community exports decoded by tools/exportfile.py
SOURCES_JSON = CAPTURE_JSON.with_name("capture.sources.json")  # mtime bookkeeping, not versioned

SV_NAME = f"{ADDON_NAME}.lua"
CAPTURE_VAR = "ForeverVOCaptureDB"



def reader_profile(entry: dict) -> dict:
    """What we know about whoever captured the entry: the capture v3 fields, else
    LEGACY_CHARACTERS (by player name), else COMMUNITY_CHARACTERS (by export origin)."""
    player = entry.get("player") or None
    known = LEGACY_CHARACTERS.get(player or "", {}) or COMMUNITY_CHARACTERS.get(entry.get("origin") or "", {})
    return {
        "player": player,
        "name": player or known.get("player"),
        "class": entry.get("class") or known.get("class"),
        "race": entry.get("race") or known.get("race"),
        "restoreName": bool(known.get("restoreName")),
    }


def character_traits(entry: dict) -> tuple[str | None, str | None, str | None]:
    """The reader's name, class and race as tokenize() wants them. The player
    name comes only from the capture itself: a community export has already
    replaced it with $n, and feeding a mapped name in here would tokenise the
    text a second time (a name like "It" would eat every "it")."""
    profile = reader_profile(entry)
    return profile["player"], profile["class"], profile["race"]


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


# Addon releases before the whole-word fix tokenised inside words, so a Paladin
# named "It" captured "w$nh" for "with" and "$Cs" for "Paladins". Blizzard text
# essentially never has a placeholder touching a letter or digit (6 of 18,126
# Classic and beta-cache lines), so that shape is treated as corruption. The
# letter must not be the B of a $B line break: "$B$B$n" is the common way a
# paragraph starts, and that alone accounts for 64 of the 70 placeholders that
# touch a letter in the raw Classic and beta-cache text.
_GLUED = re.compile(r"(?<!\$[Bb])(?<=[A-Za-z0-9])\$([NnCcRr])|\$([NnCcRr])(?=[A-Za-z0-9])")
_NAME_TOKEN = re.compile(r"\$([Nn])")
_NAME_TOKEN_UPPER = re.compile(r"\$(N)")

# From this addon version Util.Tokenize matches the reader's name
# case-sensitively. The client always renders a character name capitalised, so
# such an export can only hold the name as $N; a lowercase $n in it is the
# server's own placeholder and must not be "restored" to the reader's name.
NAME_CASE_SENSITIVE_SINCE = (0, 1, 2)


def addon_version(entry: dict) -> tuple[int, ...]:
    """The exporting addon's version as a tuple, () when unknown ("dev", or an
    export decoded before exportfile.py carried the field)."""
    return tuple(int(part) for part in re.findall(r"\d+", str(entry.get("addon") or "")))


def name_case_sensitive(entry: dict) -> bool:
    version = addon_version(entry)
    return bool(version) and version >= NAME_CASE_SENSITIVE_SINCE


def _literal(word: str, code: str) -> str:
    return word[:1].upper() + word[1:] if code.isupper() else word.lower()


def unglue_entry(entry: dict) -> dict | None:
    """Restores the literal word behind a glued placeholder, or returns None when
    the reader is unknown so the caller drops the line and it gets re-captured."""
    text = entry.get("text")
    if not text:
        return entry
    profile = reader_profile(entry)
    first_name = (profile["name"] or "").split()
    words = {"n": first_name[0] if first_name else None, "c": profile["class"], "r": profile["race"]}
    missing = False

    def put_back(match: re.Match) -> str:
        nonlocal missing
        code = match.group(1) or match.group(2)
        word = words.get(code.lower())
        if not word:
            missing = True
            return match.group(0)
        return _literal(word, code)

    fixed = _GLUED.sub(put_back, text)
    if missing:
        return None
    if profile["restoreName"] and words["n"]:
        # The export tokenised its reader's name client side, and that name is an
        # ordinary English word, so every $n it holds is that word, not the name.
        # An addon that matches the name case-sensitively can only have written
        # $N, so its lowercase $n are genuine and stay.
        token = _NAME_TOKEN_UPPER if name_case_sensitive(entry) else _NAME_TOKEN
        fixed = token.sub(lambda m: _literal(words["n"], m.group(1)), fixed)
    if fixed == text:
        return entry
    entry = dict(entry)
    entry["text"] = fixed
    return entry


# ----------------------------------------------------------------------------
# Reconciling placeholders against the source text (issue #6)
# ----------------------------------------------------------------------------
# Tokenize cannot tell the reader's expanded $c from the same word used
# literally: a Mage reading "the mage of Dalaran" captures "the $c of Dalaran".
# Where the raw text is known (the beta quest cache, the Classic database) it
# still carries the real placeholders, so a capture placeholder that aligns to a
# literal word there is put back to that word. The capture keeps everything else.

_PLACEHOLDER = re.compile(r"\$[NnCcRr]$")
_PIECES = re.compile(r"\$[NnCcRr]|\$[Bb]|\s+|[A-Za-z0-9]+|[^A-Za-z0-9\s$]+|\$")
RECONCILE_RATIO = 0.9


def _pieces(text: str) -> tuple[list[str], list[str]]:
    """(tokens, comparison keys): placeholders compare case-insensitively, $B and
    any whitespace run compare as one space, everything else lower-cased."""
    tokens = _PIECES.findall(text)
    keys = []
    for token in tokens:
        if _PLACEHOLDER.match(token):
            keys.append(token.lower())
        elif token.isspace() or token in ("$B", "$b"):
            keys.append(" ")
        else:
            keys.append(token.lower())
    return tokens, keys


def reconcile_text(text: str, source: str) -> tuple[str, int]:
    """Returns the capture text with placeholders that the source spells out as
    words restored to those words, and how many were restored. Leaves the text
    alone unless the two align closely, so a line Forever rewrote is not touched."""
    tokens, keys = _pieces(text)
    src_tokens, src_keys = _pieces(source)
    matcher = difflib.SequenceMatcher(None, keys, src_keys, autojunk=False)
    if matcher.ratio() < RECONCILE_RATIO:
        return text, 0
    restored = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "replace" or i2 - i1 != j2 - j1:
            continue
        for i, j in zip(range(i1, i2), range(j1, j2)):
            if _PLACEHOLDER.match(tokens[i]) and src_keys[j] != " " and not _PLACEHOLDER.match(src_tokens[j]):
                tokens[i] = src_tokens[j]
                restored += 1
    return "".join(tokens), restored


# ----------------------------------------------------------------------------
# Gender branches (issues #28 and #29)
# ----------------------------------------------------------------------------
# The client resolves "$g lad : lass;" before any addon sees a quest text, and
# unlike $n/$c/$r the other branch is simply not there to reverse: a quest
# accepted on a male dwarf is recorded saying "lad" and would say "lad" to
# everyone. Two ways back, both crowdsourced and both idempotent:
#
# - restore_gender: the raw text is known (the beta quest cache, Classic) and
#   the capture is exactly that text read as one sex, so the source comes back
#   with its branches (jhaubrich's #28).
# - rebuild_gender: a male and a female reading of the same line differ only
#   in short aligned runs, so the runs become branches (merge_gender). Captures
#   record the reader's sex from addon 0.1.4 on.
#
# Until a line is settled, needs_of() says which reader the pipeline still
# wants ("f" after a male reading, "mf" when nobody knows who read it). The
# pack tables carry that per event (generate.py, wa/wp/wc) and the addon
# captures and exports such a line again even though it is voiced. That is
# also the general hook for any later change in what a capture must carry:
# make needs_of() ask, and players supply the line again. Gossip is left
# resolved: it is keyed by a hash of the live text, which the client has
# already resolved, so a stored $g would never match (Util.NormalizeText
# drops $n/$c/$r but cannot restore $g).

# Classic's 315 gendered texts branch at most three times, on one or two words
# each with four outliers; anything past that is a rewording, not a form of
# address. The alignment floor is lower than RECONCILE_RATIO because a turn-in
# line can be four words long, and one of them the branch.
GENDER_BRANCHES = 3
GENDER_BRANCH_WORDS = 4   # "$g my good man:my lady;" is about the long end
GENDER_RATIO = 0.6


def _collapsed(keys: list[str]) -> list[str]:
    """Comparison keys with runs of whitespace folded into one, so the client's
    single newline and the source's "$B$B" compare the same."""
    out: list[str] = []
    for key in keys:
        if key == " " and out and out[-1] == " ":
            continue
        out.append(key)
    return out


def _same_reading(text: str, other: str) -> bool:
    return _collapsed(_pieces(text)[1]) == _collapsed(_pieces(other)[1])


_GENDER_BRANCH = re.compile(r"\$[Gg]\s*[^:;]+?\s*:\s*[^:;]+?\s*;")


def restore_gender(text: str, source: str) -> tuple[str, int]:
    """Hands the source text back when the capture is that source read as one
    sex, paragraph breaks aside. Nothing here guesses from the capture: a "lad" is
    only a branch where the raw text has one, so an ordinary "the lad who runs
    the mill" is never touched, and a line Forever reworded stays as captured."""
    if not source or not has_gender_branch(source) or has_gender_branch(text):
        return text, 0
    for variant in split_gender(source):
        if _same_reading(text, variant):
            return source, len(_GENDER_BRANCH.findall(source))
    return text, 0


def rebuild_gender(male: str, female: str) -> str | None:
    """The male reading with a "$g his:hers;" branch wherever the female reading
    differs, or None when the two are not one line read two ways: they align
    below GENDER_RATIO, a run is missing on one side rather than replaced, or
    there are more or longer branches than a form of address takes.
    Returns the male text itself when the readings only differ in whitespace."""
    m_tokens, m_keys = _pieces(male)
    f_tokens, f_keys = _pieces(female)
    matcher = difflib.SequenceMatcher(None, m_keys, f_keys, autojunk=False)
    if matcher.ratio() < GENDER_RATIO:
        return None
    out: list[str] = []
    branches = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            out.extend(m_tokens[i1:i2])
            continue
        if op != "replace":
            return None
        branches += 1
        if branches > GENDER_BRANCHES:
            return None
        his_raw, hers_raw = "".join(m_tokens[i1:i2]), "".join(f_tokens[j1:j2])
        his, hers = his_raw.strip(), hers_raw.strip()
        if not his or not hers or any(c in his + hers for c in ":;$<>"):
            return None
        if max(len(his.split()), len(hers.split())) > GENDER_BRANCH_WORDS:
            return None
        lead = his_raw[:len(his_raw) - len(his_raw.lstrip())]
        trail = his_raw[len(his_raw.rstrip()):]
        out.append(f"{lead}$g {his}:{hers};{trail}")
    return "".join(out)


def needs_of(entry: dict, kind: str, source: str | None) -> str | None:
    """Which readers the pipeline still wants this line from: None when the text
    carries its branches or both sexes have read it, "f" after a male reading,
    "m" after a female one, "mf" when the reader's sex is unknown. A capture that
    matches raw text with no branch in it needs nobody."""
    text = entry.get("text") or ""
    if kind != "quests" or not text or has_gender_branch(text):
        return None
    sex = entry.get("sex")
    if sex == "mf":
        return None
    if source and not has_gender_branch(source) and _same_reading(text, source):
        return None
    return {"m": "f", "f": "m"}.get(sex, "mf")


def merge_gender(base: dict, other: dict) -> dict:
    """What the losing entry of a merge teaches the winner about gender. A male
    and a female reading are combined into branches; a reading that equals a
    settled entry inherits its branches; and a settled entry that no longer
    matches the winner (Forever reworded the quest) is simply outvoted, so the
    line is asked for again."""
    text, other_text = base.get("text"), other.get("text")
    if not text or not other_text or has_gender_branch(text):
        return base
    mine, theirs = base.get("sex"), other.get("sex")
    if mine in ("m", "f") and theirs in ("m", "f") and mine != theirs:
        if not (fully_tokenised(base) and fully_tokenised(other)):
            return base   # a class or race word would pass for a branch
        male, female = (text, other_text) if mine == "m" else (other_text, text)
        rebuilt = rebuild_gender(male, female)
        if rebuilt is None:
            return base
        base = dict(base)
        base["text"] = rebuilt if has_gender_branch(rebuilt) else text
        base["sex"] = "mf"
        return base
    if theirs == "mf":
        if has_gender_branch(other_text):
            variants = split_gender(other_text)
            readings = [variants[0 if mine == "m" else 1]] if mine in ("m", "f") else list(variants)
            if any(_same_reading(text, v) for v in readings):
                base = dict(base)
                base["text"], base["sex"] = other_text, "mf"
        elif _same_reading(text, other_text):
            base = dict(base)
            base["sex"] = "mf"
        return base
    if mine is None and theirs in ("m", "f") and _same_reading(text, other_text):
        base = dict(base)
        base["sex"] = theirs
    return base


class SourceTexts:
    """Raw quest and gossip text from tools/data/bulk/*.json, for reconciliation."""

    def __init__(self, bulk_dir: Path = DATA_DIR / "bulk"):
        self.quests: dict[str, str] = {}
        self.gossip: dict[str, list[str]] = {}
        self.speakers: dict[str, dict] = {}            # quest key -> {npc, name, isObject} per Classic
        self.quest_creatures: dict[str, set[str]] = {}  # quest ID -> creature keys at either end
        self.loaded: list[str] = []
        # capture > questcache > classic: first source to name a key wins
        for name in ("questcache", "classic"):
            path = bulk_dir / f"{name}.json"
            if not path.exists():
                print(f"note: {path.relative_to(ROOT)} missing, placeholders not reconciled against {name}")
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for key, entry in data.get("quests", {}).items():
                if entry.get("text"):
                    self.quests.setdefault(str(key), entry["text"])
                if name == "classic" and entry.get("event"):
                    self.speakers[str(key)] = {f: entry.get(f) for f in ("npc", "name", "isObject")}
                    npc = str(entry.get("npc") or "")
                    if npc and not npc.startswith("-"):
                        self.quest_creatures.setdefault(str(entry.get("questID")), set()).add(npc)
            for key, entry in data.get("gossip", {}).items():
                if entry.get("text"):
                    self.gossip.setdefault(str(key).split("|", 1)[0], []).append(entry["text"])
            self.loaded.append(name)

    def quest(self, key: str) -> str | None:
        return self.quests.get(key)

    def gossip_for(self, key: str, text: str) -> str | None:
        """The same speaker's closest Classic line, if any is close enough."""
        best, best_ratio = None, RECONCILE_RATIO
        _, keys = _pieces(text)
        for candidate in self.gossip.get(str(key).split("|", 1)[0], ()):
            ratio = difflib.SequenceMatcher(None, keys, _pieces(candidate)[1], autojunk=False).ratio()
            if ratio >= best_ratio:
                best, best_ratio = candidate, ratio
        return best


def reconcile_entry(entry: dict, kind: str, key: str, sources: SourceTexts | None) -> tuple[dict, int]:
    text = entry.get("text")
    if not text or sources is None:
        return entry, 0
    source = sources.quest(key) if kind == "quests" else sources.gossip_for(key, text)
    if source is None:
        return entry, 0
    fixed, restored = reconcile_text(text, source)
    if not restored:
        return entry, 0
    entry = dict(entry)
    entry["text"] = fixed
    return entry, restored


def reattribute_entry(entry: dict, kind: str, key: str, sources: SourceTexts | None) -> tuple[dict, bool]:
    """A quest text the client left unattributed, pinned by the addon to the last
    NPC the reader talked to.

    Addons before 0.1.3 took the "npc" unit, which outlives its dialog: the Corpse
    Laden Boat's turn-in text was captured as High Executor Hadrec, three minutes
    after his frame closed, and Admiral Proudmoore's orders, read beside Gar'Thok,
    as him. Where Classic says the text belongs to an object or an item and the
    capture names a creature that stands at the quest's other end, the capture is
    that artefact: the speaker goes back to Classic's, and the narrator reads it."""
    if kind != "quests" or sources is None:
        return entry, False
    known = sources.speakers.get(key)
    npc = str(entry.get("npc") or "")
    if not known or not known.get("isObject") or not npc or npc.startswith("-"):
        return entry, False
    if npc not in sources.quest_creatures.get(str(entry.get("questID")), set()):
        return entry, False
    entry = dict(entry)
    entry.pop("npc", None)
    if known.get("npc"):
        entry["npc"] = known["npc"]
    entry["name"] = known.get("name") or entry.get("name")
    entry["isObject"] = True
    return entry, True


class Repairs:
    dropped = 0
    reconciled = 0
    restored = 0
    reattributed = 0
    gendered = 0
    branches = 0


def repair_entry(entry: dict, kind: str, key: str, sources: SourceTexts | None, stats: Repairs) -> dict | None:
    """tokenize -> unglue -> reconcile -> regender -> reattribute -> needs. Idempotent,
    so it runs over everything on every ingest; None means the line is unusable and
    should be dropped."""
    entry = tokenize_entry(entry)
    entry = unglue_entry(entry)
    if entry is None:
        stats.dropped += 1
        return None
    entry, restored = reconcile_entry(entry, kind, key, sources)
    if restored:
        stats.reconciled += 1
        stats.restored += restored
    source = sources.quest(key) if sources is not None and kind == "quests" else None
    if source:
        fixed, branches = restore_gender(entry.get("text") or "", source)
        if branches:
            entry = dict(entry)
            entry["text"] = fixed
            stats.gendered += 1
            stats.branches += branches
    entry, reattributed = reattribute_entry(entry, kind, key, sources)
    if reattributed:
        stats.reattributed += 1
    needs = needs_of(entry, kind, source)
    if needs != entry.get("needs"):
        entry = dict(entry)
        entry.pop("needs", None)
        if needs:
            entry["needs"] = needs
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


def fully_tokenised(entry: dict) -> bool:
    """True when the capture had the reader's class and race to hand (capture v3,
    or an export, which tokenises client side): its literal words are literal."""
    return entry.get("source") == "community" or bool(entry.get("class") and entry.get("race"))


def merge_entry(store: dict, key: str, entry: dict) -> bool:
    """The newer capture wins the line, but the other reading still teaches it
    what a single reader cannot see: a class or race word, and a gender branch."""
    old = store.get(key)
    if old is None:
        store[key] = entry
        return True
    newer = (entry.get("time") or 0) >= (old.get("time") or 0)
    base, other = (dict(entry), old) if newer else (dict(old), entry)
    if newer:
        base["firstSeen"] = old.get("firstSeen", old.get("time"))
    if (base.get("player") != other.get("player") and base.get("text") and other.get("text")
            and fully_tokenised(base) and fully_tokenised(other)):
        # Two readers of different class or race: a placeholder only one of
        # them saw is that reader's own class or race used as a plain word
        base["text"], _ = reconcile_text(base["text"], other["text"])
    base = merge_gender(base, other)
    changed = base != old
    store[key] = base
    return changed


def ingest_file(capture: dict, path: Path, sources: SourceTexts | None, stats: Repairs) -> tuple[int, int, int]:
    if path.suffix == ".json":
        db = json.loads(path.read_text(encoding="utf-8"))
    else:
        variables = parse_saved_variables(path.read_text(encoding="utf-8", errors="replace"))
        db = variables.get(CAPTURE_VAR)
    if not isinstance(db, dict):
        return (0, 0, 0)
    # Community exports: the issue comment they came from, and the addon that
    # wrote them (absent before 0.1.2), both stamped on each entry
    stamp = {field: db[field] for field in ("origin", "addon") if db.get(field)}
    quests = gossip = npcs = 0
    for key, entry in (db.get("quests") or {}).items():
        entry = repair_entry({**entry, **stamp} if stamp else entry, "quests", str(key), sources, stats)
        if entry is not None:
            quests += merge_entry(capture["quests"], str(key), entry)
    for key, entry in (db.get("gossip") or {}).items():
        entry = repair_entry({**entry, **stamp} if stamp else entry, "gossip", str(key), sources, stats)
        if entry is not None:
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


def backfill(capture: dict, sources: SourceTexts | None, stats: Repairs) -> tuple[int, int]:
    """Re-runs the repairs over text already in capture.json and re-keys any gossip
    line whose hash moves as a result. Idempotent, so it just runs on every ingest:
    it is what repairs everything captured before the addon recorded class and
    race, and everything captured by a client that glued placeholders."""
    quests, rebuilt_quests = 0, {}
    for key, entry in capture["quests"].items():
        fixed = repair_entry(entry, "quests", key, sources, stats)
        if fixed is None:
            quests += 1
            continue
        if fixed != entry:
            quests += 1
        rebuilt_quests[key] = fixed
    capture["quests"] = rebuilt_quests
    gossip, rebuilt = 0, {}
    for key, entry in capture["gossip"].items():
        fixed = repair_entry(entry, "gossip", key, sources, stats)
        if fixed is None:
            gossip += 1
            continue
        new_key = gossip_key(key, fixed)
        if fixed.get("text") != entry.get("text") or new_key != key:
            gossip += 1
        rebuilt[new_key] = fixed
    capture["gossip"] = rebuilt
    return quests, gossip


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    files = [Path(a) for a in argv] or (find_saved_variable_files() + sorted(CAPTURES_DIR.glob("*.json")))
    if not files:
        print(f"no {SV_NAME} files found under {BETA_DIR / 'WTF' / 'Account'}")
        return 1
    capture = load_capture()
    sources = SourceTexts()
    stats = Repairs()
    for path in files:
        seen = capture["sources"].get(str(path))
        stat = path.stat()
        if seen and seen["mtime"] == stat.st_mtime and seen["size"] == stat.st_size:
            print(f"unchanged  {path}")
            continue
        quests, gossip, npcs = ingest_file(capture, path, sources, stats)
        print(f"ingested   {path}: {quests} quest, {gossip} gossip, {npcs} npc changes")

    repaired = backfill(capture, sources, stats)
    if any(repaired):
        print(f"repaired   {repaired[0]} quest, {repaired[1]} gossip texts ($n/$c/$r put back, glued placeholders "
              f"and literal words restored)")
    if stats.reconciled:
        print(f"reconciled {stats.restored} placeholder(s) in {stats.reconciled} texts against {', '.join(sources.loaded)}")
    if stats.dropped:
        print(f"dropped    {stats.dropped} texts with glued placeholders from an unknown reader (re-capture them)")
    if stats.reattributed:
        print(f"reattributed {stats.reattributed} quest texts from a lingering NPC to the object or item Classic names")
    if stats.gendered:
        print(f"regendered {stats.branches} $g branch(es) in {stats.gendered} quest texts from the raw source text")
    needing = sum(1 for e in capture["quests"].values() if e.get("needs"))
    settled = sum(1 for e in capture["quests"].values() if e.get("sex") == "mf")
    if needing or settled:
        print(f"gender     {needing} quest texts still want a reader of the other sex (or any reader); "
              f"{settled} settled by readings of both")

    CAPTURE_JSON.parent.mkdir(parents=True, exist_ok=True)
    seen_files = capture.pop("sources")
    CAPTURE_JSON.write_text(json.dumps(capture, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    SOURCES_JSON.write_text(json.dumps(seen_files, indent=1, sort_keys=True), encoding="utf-8")
    missing_q = sum(1 for e in capture["quests"].values() if not e.get("found"))
    missing_g = sum(1 for e in capture["gossip"].values() if not e.get("found"))
    print(f"capture.json: {len(capture['quests'])} quest texts ({missing_q} without audio), "
          f"{len(capture['gossip'])} gossip texts ({missing_g} without audio), {len(capture['npcs'])} NPCs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
