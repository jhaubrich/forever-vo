"""The capture repairs that put the server's placeholders back, and the trust
rules that decide which lines are asked for again."""
from __future__ import annotations

import json
from pathlib import Path

from tools.config import Reader, Readers
from tools.ingest import (
    Repairs,
    SourceTexts,
    flawed,
    gossip_key,
    needs_of,
    reconcile_text,
    repair_entry,
    superseded_gossip,
    tokenize_entry,
    trusted,
)
from tools.textkey import text_key, tokenize

READERS = Readers(trusted_since=(0, 1, 4), legacy={
    "Myrlin Fixpoint": Reader(**{"class": "Mage", "race": "Orc"}),
    "Pellinore Fixpoint": Reader(**{"class": "Hunter"}),
})


def test_multi_word_race_matches_its_last_word() -> None:
    text = "Can Zamja help you, skyborne? The Windshaper Skyborne are welcome; windshapers too."
    assert tokenize(text, "Pellinore", "Hunter", "Windshaper Skyborne") == (
        "Can Zamja help you, $r? The $R are welcome; windshapers too.")
    assert tokenize(text, "Pellinore", "Hunter", "Windshaper Skyborne", short_race=False) == (
        "Can Zamja help you, skyborne? The $R are welcome; windshapers too.")
    assert tokenize("An orc and an Orc.", "Myrlin", "Mage", "Orc") == "An $r and an $R."


def test_ingest_applies_the_short_race_rule_only_from_the_fixed_addon() -> None:
    old = {"text": "Help you, skyborne?", "player": "Pellinore", "class": "Hunter",
           "race": "Windshaper Skyborne", "addon": "0.1.4"}
    assert tokenize_entry(old, READERS)["text"] == "Help you, skyborne?"
    new = {**old, "addon": "0.1.5"}
    assert tokenize_entry(new, READERS)["text"] == "Help you, $r?"
    # the gossip key follows the same rule, so an old entry keys as its literal text
    assert gossip_key("3399|x", old, READERS) == "3399|" + text_key("Help you, skyborne?")
    assert gossip_key("3399|x", new, READERS) == "3399|" + text_key("Help you, $r?")


def test_legacy_reader_race_puts_the_placeholder_back() -> None:
    entry = {"text": "You're just the orc I'm looking for!", "player": "Myrlin Fixpoint"}
    assert tokenize_entry(entry, READERS)["text"] == "You're just the $r I'm looking for!"


def test_reconcile_against_raw_text_goes_both_ways() -> None:
    raw = "Can Zamja help you, $r? Sit down and tell me what brings you here, $N."
    heard = "Can Zamja help you, skyborne? Sit down and tell me what brings you here, Pellinore."
    # a literal word aligned to a placeholder in the raw text is that placeholder
    assert reconcile_text(heard, raw, raw=True) == (raw, 2)
    # a placeholder aligned to a plain raw word is still restored to the word
    assert reconcile_text("The $c of Dalaran keeps the tower.", "The mage of Dalaran keeps the tower.", raw=True) == (
        "The mage of Dalaran keeps the tower.", 1)
    # between two captures only the placeholder side is corrected
    assert reconcile_text("The mage of Dalaran keeps the tower.", "The $c of Dalaran keeps the tower.") == (
        "The mage of Dalaran keeps the tower.", 0)
    # a rewording is left alone
    assert reconcile_text("Zamja cannot help anyone today, skyborne. Go away.", raw, raw=True)[1] == 0


def test_short_race_flaw_marks_only_its_blast_radius() -> None:
    hit = {"text": "Help you, skyborne?", "player": "Pellinore", "class": "Hunter",
           "race": "Windshaper Skyborne", "addon": "0.1.4"}
    assert flawed(hit, READERS) and not trusted(hit, READERS)
    assert needs_of(hit, "quests", None, READERS) == "mf"
    # the same reader's line without the word is trusted as before
    clean = {**hit, "text": "Help you, friend?"}
    assert not flawed(clean, READERS) and trusted(clean, READERS)
    # a one-word race is not in the radius, nor is the fixed addon
    assert not flawed({**hit, "race": "Orc", "text": "Help you, orc?"}, READERS)
    assert not flawed({**hit, "addon": "0.1.5"}, READERS)
    # a legacy reader with no race in the map is untrusted anyway
    legacy = {"text": "Help you, skyborne?", "player": "Pellinore Fixpoint"}
    assert not flawed(legacy, READERS) and not trusted(legacy, READERS)


def test_repair_takes_a_flawed_line_out_of_the_radius_when_the_raw_text_fixes_it(tmp_path: Path) -> None:
    raw = "Can Zamja help you, $r? Sit down and tell me what brings you here."
    (tmp_path / "questcache.json").write_text(json.dumps({"quests": {"1-accept": {"text": raw}}}), encoding="utf-8")
    sources = SourceTexts(bulk_dir=tmp_path)
    entry = {"text": "Can Zamja help you, skyborne? Sit down and tell me what brings you here.",
             "player": "Pellinore", "class": "Hunter", "race": "Windshaper Skyborne", "addon": "0.1.4", "sex": "m"}
    fixed = repair_entry(entry, "quests", "1-accept", sources, Repairs(), READERS)
    assert fixed is not None and fixed["text"] == raw
    assert trusted(fixed, READERS) and fixed.get("needs") is None
    # the same line with no raw text stays in the radius and is asked for again
    unfixed = repair_entry(entry, "quests", "2-accept", sources, Repairs(), READERS)
    assert unfixed is not None and unfixed["text"] == entry["text"] and unfixed.get("needs") == "mf"


def test_untrusted_capture_that_matches_classic_with_its_speaker_needs_nobody() -> None:
    raw = "What do we have here? You look as though you might need gloves, $N."
    legacy = {"text": "What do we have here? You look as though you might need gloves, $n.",
              "player": "Spron Limbertorque", "npc": "658"}
    sten = {"npc": "658", "name": "Sten Stoutarm", "isObject": None}
    assert not trusted(legacy, READERS)
    assert needs_of(legacy, "quests", raw, READERS, sten) is None
    # a different speaker, no speaker, a branch in the source, or another text keep it open
    assert needs_of(legacy, "quests", raw, READERS, {**sten, "npc": "659"}) == "mf"
    assert needs_of(legacy, "quests", raw, READERS, None) == "mf"
    assert needs_of(legacy, "quests", "Gloves for you, $g lad:lass;.", READERS, sten) == "mf"
    assert needs_of({**legacy, "text": "Forever reworded this one."}, "quests", raw, READERS, sten) == "mf"
    # an object on both sides counts as agreement
    plaque = {**legacy, "npc": "-100", "isObject": True}
    assert needs_of(plaque, "quests", raw, READERS, {"npc": "-100", "name": "Plaque", "isObject": True}) is None
    assert needs_of({**legacy, "npc": "658"}, "quests", raw, READERS, {"npc": "-100", "isObject": True}) == "mf"


def test_fixed_addon_supersedes_a_flawed_gossip_reading() -> None:
    reader = {"player": "Pellinore", "class": "Hunter", "race": "Windshaper Skyborne"}
    gossip = {
        "3399|aaaa": {"text": "Can Zamja help you, skyborne?", **reader, "addon": "0.1.4"},
        "3399|bbbb": {"text": "Can Zamja help you, $r?", **reader, "addon": "0.1.5"},
        # too short for the alignment floor, but the fixed tokenisation matches exactly
        "3400|cccc": {"text": "Help you, skyborne?", **reader, "addon": "0.1.4"},
        "3400|dddd": {"text": "Help you, $r?", **reader, "addon": "0.1.5"},
        # a different line of the same speaker stays
        "3400|eeee": {"text": "Go away, skyborne, I am busy.", **reader, "addon": "0.1.4"},
    }
    assert superseded_gossip(gossip, READERS) == {"3399|aaaa", "3400|cccc"}
