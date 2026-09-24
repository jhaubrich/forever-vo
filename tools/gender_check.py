"""Checks the $g branch repair in ingest.py against fixed cases and the real
Classic text. Run it after touching rebuild_gender, restore_gender, needs_of or
merge_gender:

    ./tools/run.sh tools/gender_check.py
"""
from __future__ import annotations

import json
import sys

from tools.config import DATA_DIR
from tools.ingest import Repairs, SourceTexts, merge_entry, needs_of, rebuild_gender, repair_entry, restore_gender

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got == want:
        print(f"ok   {name}")
    else:
        failures += 1
        print(f"FAIL {name}\n   got:  {got!r}\n   want: {want!r}")


def community(text: str, sex: str | None = None, time: int | None = None) -> dict:
    entry = {"text": text, "source": "community", "class": None, "race": None}
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
        entry = repair_entry(community(male, "m"), "quests", "233-accept", sources, Repairs())
        check("repair restores 233", (entry["text"] == classic, entry.get("needs")), (True, None))
        entry = repair_entry(community("Some Forever-only text, lad.", "m"), "quests", "999999-accept", sources, Repairs())
        check("repair marks needs", entry.get("needs"), "f")
    else:
        print(f"skip classic cases: {classic_path} missing (run classicdb.py)")

    # needs_of: who is still wanted
    check("needs unknown", needs_of({"text": "Hi lad."}, "quests", None), "mf")
    check("needs m", needs_of({"text": "Hi lad.", "sex": "m"}, "quests", None), "f")
    check("needs f", needs_of({"text": "Hi lass.", "sex": "f"}, "quests", None), "m")
    check("needs settled", needs_of({"text": "Hi lad.", "sex": "mf"}, "quests", None), None)
    check("needs tagged", needs_of({"text": "Hi $g lad:lass;."}, "quests", None), None)
    check("needs equals plain source", needs_of({"text": "Hi $n.\n\nBye."}, "quests", "Hi $N.$B$BBye."), None)
    check("needs differs from source", needs_of({"text": "Hi $n. Bye now."}, "quests", "Hi $N.$B$BBye."), "mf")
    check("needs gossip", needs_of({"text": "Hi lad."}, "gossip", None), None)

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

    print(f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
