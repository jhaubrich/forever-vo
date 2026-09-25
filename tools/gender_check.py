"""Checks the $g branch repair and the self-repair rules in ingest.py against
fixed cases and the real Classic text. Run it after touching rebuild_gender,
restore_gender, needs_of, merge_gender, merge_entry, capture_rank or
superseded_gossip:

    ./tools/run.sh tools/gender_check.py
"""
from __future__ import annotations

import json
import sys

from tools.config import DATA_DIR, Readers
from tools.ingest import (
    Repairs,
    SourceTexts,
    backfill,
    merge_entry,
    needs_of,
    rebuild_gender,
    repair_entry,
    restore_gender,
    superseded_gossip,
)

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got == want:
        print(f"ok   {name}")
    else:
        failures += 1
        print(f"FAIL {name}\n   got:  {got!r}\n   want: {want!r}")


# The rules are checked against a fixed trust line, not the repository's, so the
# cases below stay meaningful when [readers] trusted_since moves on.
READERS = Readers(trusted_since=(0, 1, 4))
TRUSTED = {"addon": "0.1.4"}   # a capture the pipeline takes at face value


def community(text: str, sex: str | None = None, time: int | None = None) -> dict:
    entry = {"text": text, "source": "community", "class": None, "race": None, **TRUSTED}
    if sex:
        entry["sex"] = sex
    if time:
        entry["time"] = time
    return entry


def main() -> int:
    # rebuild_gender: two readings into one tagged text
    check("lad/lass", rebuild_gender("Hello, lad. Sit down.", "Hello, lass. Sit down."), "Hello, $g lad:lass;. Sit down.")
    check("multi word", rebuild_gender("Well met, my good man!", "Well met, my lady!"), "Well met, my $g good man:lady;!")
    check("two branches", rebuild_gender("He said so. Thank him, sir.", "She said so. Thank her, sir."),
          "$g He:She; said so. Thank $g him:her;, sir.")
    check("identical", rebuild_gender("Same text.\n\nHere.", "Same text.\n\nHere."), "Same text.\n\nHere.")
    check("whitespace only", rebuild_gender("Same text.\n\nHere.", "Same text.\nHere."), "Same text.\n\nHere.")
    check("rewrite", rebuild_gender("Go to the mill and kill ten kobolds for me now please.",
                                    "Go to the farm and bring me twenty apples, then rest."), None)
    check("insert", rebuild_gender("Hello there.", "Hello there, lass."), None)
    check("long branch", rebuild_gender("a b c d e f g h i j k l m n o p", "a b c d e f g h X Y Z W V m n o p"), None)
    check("too many branches", rebuild_gender("a1 b c1 d e1 f g1 h i j k l", "a2 b c2 d e2 f g2 h i j k l"), None)

    # restore_gender: a single reading of Classic's quest 233 comes back tagged
    classic_path = DATA_DIR / "bulk" / "classic.json"
    if classic_path.exists():
        classic = json.loads(classic_path.read_text(encoding="utf-8"))["quests"]["233-accept"]["text"]
        male = classic.replace("$g lad : lass;", "lad").replace("$b$b", "\n\n")
        female = classic.replace("$g lad : lass;", "lass").replace("$b$b", "\n\n")
        check("restore male", restore_gender(male, classic), (classic, 1))
        check("restore female", restore_gender(female, classic), (classic, 1))
        reworded = male.replace("favor", "favour and a half")
        check("restore reworded", restore_gender(reworded, classic), (reworded, 0))
        check("restore no-op", restore_gender(classic, classic), (classic, 0))
        sources = SourceTexts()
        entry = repair_entry(community(male, "m"), "quests", "233-accept", sources, Repairs(), READERS)
        assert entry is not None
        check("repair restores 233", (entry["text"] == classic, entry.get("needs")), (True, None))
        entry = repair_entry(community("Some Forever-only text, lad.", "m"), "quests", "999999-accept", sources, Repairs(), READERS)
        assert entry is not None
        check("repair marks needs", entry.get("needs"), "f")
    else:
        print(f"skip classic cases: {classic_path} missing (run classicdb.py)")

    # needs_of: who is still wanted
    check("needs unknown", needs_of({"text": "Hi lad.", **TRUSTED}, "quests", None, READERS), "mf")
    check("needs m", needs_of({"text": "Hi lad.", "sex": "m", **TRUSTED}, "quests", None, READERS), "f")
    check("needs f", needs_of({"text": "Hi lass.", "sex": "f", **TRUSTED}, "quests", None, READERS), "m")
    check("needs settled", needs_of({"text": "Hi lad.", "sex": "mf", **TRUSTED}, "quests", None, READERS), None)
    check("needs tagged", needs_of({"text": "Hi $g lad:lass;.", **TRUSTED}, "quests", None, READERS), None)
    check("needs equals plain source", needs_of({"text": "Hi $n.\n\nBye.", **TRUSTED}, "quests", "Hi $N.$B$BBye.", READERS), None)
    check("needs differs from source", needs_of({"text": "Hi $n. Bye now.", **TRUSTED}, "quests", "Hi $N.$B$BBye.", READERS), "mf")
    check("needs gossip", needs_of({"text": "Hi lad."}, "gossip", None, READERS), None)

    # merge_entry: what a second reading teaches the first
    store: dict = {}
    merge_entry(store, "1-accept", community("Hello, lad. Sit down.", "m"))
    check("first stays", store["1-accept"]["text"], "Hello, lad. Sit down.")
    changed = merge_entry(store, "1-accept", community("Hello, lass. Sit down.", "f"))
    check("m+f merged", (changed, store["1-accept"]["text"], store["1-accept"]["sex"]),
          (True, "Hello, $g lad:lass;. Sit down.", "mf"))
    merge_entry(store, "1-accept", community("Hello, lad. Sit down.", "m"))
    check("settled keeps tags", (store["1-accept"]["text"], store["1-accept"]["sex"]), ("Hello, $g lad:lass;. Sit down.", "mf"))
    # a later, timed local capture of a rewording outvotes the settled entry
    merge_entry(store, "1-accept", {**community("Greetings, lad. Please sit.", "m", time=5),
                                    "class": "Mage", "race": "Gnome", "player": "X"})
    check("rewrite outvotes", (store["1-accept"]["text"], store["1-accept"]["sex"]), ("Greetings, lad. Please sit.", "m"))
    merge_entry(store, "1-accept", community("Hello, lass. Sit down.", "f"))
    check("old reading ignored", (store["1-accept"]["text"], store["1-accept"]["sex"]), ("Greetings, lad. Please sit.", "m"))
    # a female reading of the new text combines even though it loses on time
    merge_entry(store, "1-accept", community("Greetings, lass. Please sit.", "f"))
    check("loser teaches winner", (store["1-accept"]["text"], store["1-accept"]["sex"]),
          ("Greetings, $g lad:lass;. Please sit.", "mf"))
    store = {}
    merge_entry(store, "2-accept", community("No gendered words here.", "m"))
    merge_entry(store, "2-accept", community("No gendered words here.", "f"))
    check("identical settles", (store["2-accept"]["text"], store["2-accept"]["sex"]), ("No gendered words here.", "mf"))
    store = {}
    merge_entry(store, "3-accept", community("Hi lad."))
    merge_entry(store, "3-accept", community("Hi lad.", "m"))
    check("learns sex", store["3-accept"].get("sex"), "m")
    # a legacy capture without class and race could pass a class word off as a branch
    store = {}
    merge_entry(store, "4-accept", {"text": "Hi rogue.", "sex": "m", "time": 1})
    merge_entry(store, "4-accept", {"text": "Hi mage.", "sex": "f", "time": 2})
    check("untokenised not combined", (store["4-accept"]["text"], store["4-accept"].get("sex")), ("Hi mage.", "f"))

    # self-repair: a capture from a fixed addon outranks a flawed earlier one,
    # whatever the order in time, and the line is wanted from anyone until then
    old = {"text": "Hello $n, wrong.", "npc": "111", "time": 9000, "player": "Q", "class": "Rogue", "race": "Human", "sex": "m"}
    fixed = {"text": "Hello $n, right.", "npc": "222", "source": "community", "sex": "m", "addon": "0.1.4"}
    store = {"5-accept": dict(old)}
    merge_entry(store, "5-accept", fixed)
    check("trusted beats untimed old", (store["5-accept"]["text"], store["5-accept"]["npc"]), ("Hello $n, right.", "222"))
    merge_entry(store, "5-accept", {**old, "time": 99999})
    check("later old addon loses", (store["5-accept"]["text"], store["5-accept"]["npc"]), ("Hello $n, right.", "222"))
    merge_entry(store, "5-accept", {**old, "addon": "0.1.2", "time": 99999})
    check("older addon loses", store["5-accept"]["npc"], "222")
    merge_entry(store, "5-accept", {**fixed, "text": "Hello $n, reworded.", "time": 5})
    merge_entry(store, "5-accept", {**fixed, "text": "Hello $n, right.", "time": 4})
    check("same addon, later reading wins", store["5-accept"]["text"], "Hello $n, reworded.")
    merge_entry(store, "5-accept", {**fixed, "text": "Hello $n, newest.", "addon": "0.2.0"})
    check("newer addon beats timed", store["5-accept"]["text"], "Hello $n, newest.")
    check("needs untrusted", needs_of({"text": "Hi $n.", "sex": "mf"}, "quests", "Hi $N.", READERS), "mf")
    check("needs untrusted tagged", needs_of({"text": "Hi $g lad:lass;.", "addon": "0.1.3"}, "quests", None, READERS), "mf")
    check("needs trusted m", needs_of({"text": "Hi lad.", "sex": "m", "addon": "0.1.4"}, "quests", None, READERS), "f")
    check("needs trusted source", needs_of({"text": "Hi $n.", "addon": "0.1.4"}, "quests", "Hi $N.", READERS), None)
    check("needs trusted gossip", needs_of({"text": "Hi lad.", "addon": "0.1.4"}, "gossip", None, READERS), None)
    # an untrusted female reading still combines with a trusted male one
    store = {"6-accept": {"text": "Hello, lass.", "sex": "f", "source": "community", "addon": "0.1.3"}}
    merge_entry(store, "6-accept", {"text": "Hello, lad.", "sex": "m", "source": "community", "addon": "0.1.4"})
    check("untrusted teaches trusted", (store["6-accept"]["text"], store["6-accept"]["addon"]),
          ("Hello, $g lad:lass;.", "0.1.4"))
    # superseded gossip: an untrusted near-duplicate of a trusted line goes
    # (a pre-v3 capture heard "rogue" where the trusted one heard $c; the hash
    # keeps the literal word, so the two sit under different keys)
    gossip = {
        "7|aaaa": {"text": "Greetings, rogue. What brings you to Goldshire on this fine day?", "addon": "0.1.2"},
        "7|bbbb": {"text": "Greetings, $c. What brings you to Goldshire on this fine day?", "addon": "0.1.4"},
        "7|cccc": {"text": "Have you seen my sister? She went to the mill.", "addon": "0.1.2"},
        "8|dddd": {"text": "Greetings, rogue. What brings you to Goldshire on this fine day?", "addon": "0.1.2"},
    }
    check("superseded gossip", superseded_gossip(gossip, READERS), {"7|aaaa"})
    # a known flaw narrows trust to its blast radius: a Skyborne's pre-0.1.5 line
    # that still says "skyborne" is asked for again and can be superseded, while
    # the same reader's other lines stay trusted
    skyborne = {"player": "Pellinore", "class": "Hunter", "race": "Windshaper Skyborne", "addon": "0.1.4"}
    check("flaw untrusted", needs_of({"text": "Help you, skyborne?", "sex": "m", **skyborne}, "quests", None, READERS), "mf")
    check("flaw outside radius", needs_of({"text": "Help you, friend?", "sex": "m", **skyborne}, "quests", None, READERS), "f")
    check("flaw superseded", superseded_gossip({
        "9|aaaa": {"text": "Help you, skyborne?", **skyborne},
        "9|bbbb": {"text": "Help you, $r?", **skyborne, "addon": "0.1.5"},
        "9|cccc": {"text": "Go away, skyborne, I am busy.", **skyborne},
    }, READERS), {"9|aaaa"})
    store = {"version": 2, "quests": {}, "gossip": dict(gossip), "npcs": {}}
    stats = Repairs()
    backfill(store, None, stats, READERS)
    check("backfill drops superseded", (sorted(e["addon"] for e in store["gossip"].values()), stats.superseded),
          (["0.1.2", "0.1.2", "0.1.4"], 1))

    print(f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
