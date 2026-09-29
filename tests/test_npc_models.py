"""A speaker's model, which is its race for Forever's own NPCs: which readings
count, which the pack records, and the reader's traits an export carries (#352)."""

from __future__ import annotations

from tools.config import Voices, load_config
from tools.exportfile import to_capture
from tools.generate import Item, VoiceCatalog, model_cast
from tools.ingest import merge_npc

GOBLIN, TAUREN, ORC = 119376, 968705, 917116


def test_an_old_reading_is_used_until_a_trusted_one_arrives() -> None:
    npc = merge_npc({}, {"name": "Fizzlefuse", "modelFileID": TAUREN}, "a", "0.1.5")
    assert npc["modelFileID"] == TAUREN
    npc = merge_npc(npc, {"modelFileID": GOBLIN}, "b", "0.1.7")
    assert npc["modelFileID"] == GOBLIN
    # a later export from an old addon re-sends its misread record
    npc = merge_npc(npc, {"modelFileID": ORC}, "c", "0.1.6")
    assert npc["modelFileID"] == GOBLIN
    assert npc["name"] == "Fizzlefuse"


def test_the_addon_on_the_record_wins_over_the_files() -> None:
    npc = merge_npc({}, {"modelFileID": GOBLIN, "addon": "0.1.7"}, "local", None)
    assert npc["modelReads"] == {"local": GOBLIN}
    assert "addon" not in npc


def test_trusted_readers_vote_and_a_tie_keeps_the_model() -> None:
    npc = merge_npc({}, {"modelFileID": GOBLIN}, "a", "0.1.7")
    npc = merge_npc(npc, {"modelFileID": ORC}, "b", "0.1.7")
    assert npc["modelFileID"] == GOBLIN
    npc = merge_npc(npc, {"modelFileID": ORC}, "c", "0.1.7")
    assert npc["modelFileID"] == ORC
    # a reader's own re-read (a saved-variables file written again) counts once
    npc = merge_npc(npc, {"modelFileID": ORC}, "c", "0.1.7")
    assert npc["modelReads"] == {"a": GOBLIN, "b": ORC, "c": ORC}
    npc = merge_npc(npc, {"modelFileID": GOBLIN}, "d", "0.1.7")
    assert npc["modelFileID"] == ORC


def item(npc: dict, speaker: str = "248199") -> Item:
    catalog = VoiceCatalog(load_config().model_copy(update={"voices": Voices()}))
    return Item("gossip", "k", {"npc": speaker, "text": "Yo."}, npc, catalog)


def test_the_pack_records_a_model_only_where_it_chose_the_voice() -> None:
    assert model_cast(item({"modelFileID": GOBLIN, "sex": 2})) == GOBLIN
    assert model_cast(item({"modelFileID": GOBLIN, "displayID": 7102})) is None
    assert model_cast(item({"modelFileID": GOBLIN, "isObject": True})) is None
    assert model_cast(item({"sex": 2})) is None


def test_a_pinned_speaker_has_no_model_to_compare() -> None:
    config = load_config()
    catalog = VoiceCatalog(config)
    pinned = Item("gossip", "k", {"npc": "248200", "text": "Yo."}, {}, catalog)
    pinned.npc = {"modelFileID": ORC}
    assert model_cast(pinned) is None


def test_an_export_carries_the_readers_class_and_race() -> None:
    data = {
        "v": 1,
        "addon": "0.1.7",
        "lines": [{"k": "gossip", "x": "Hi, $c.", "n": "3", "c": "Mage", "r": "Orc"}],
        "npcs": {"3": {"modelFileID": GOBLIN, "addon": "0.1.7"}},
    }
    capture = to_capture(data, "issue-1")
    (entry,) = capture["gossip"].values()
    assert (entry["class"], entry["race"]) == ("Mage", "Orc")
    assert capture["npcs"]["3"]["addon"] == "0.1.7"
