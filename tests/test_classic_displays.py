"""A captured NPC gets its Classic display ID only where the models agree."""

from __future__ import annotations

import sqlite3

from tools.classicdb import creature_displays, latest_patch_rows
from tools.config import Voices, load_config
from tools.generate import Item, VoiceCatalog, fill_displays, model_cast

VOLJIN_MODEL = 1022938
MODELS = {10357: VOLJIN_MODEL, 3423: 878772}  # display -> the model file it draws


def test_a_display_is_taken_only_when_its_model_is_the_one_captured() -> None:
    npcs = {
        "10540": {"name": "Vol'jin", "modelFileID": VOLJIN_MODEL, "sex": 2},
        # Forever remodelled him: Classic's dwarf display is not what players see
        "1256": {"name": "Quarrymaster Thesten", "modelFileID": 118355, "sex": 2},
        "1253": {"name": "Father Gavin", "displayID": 1616, "modelFileID": 5},
        "-1": {"name": "a book", "isObject": True, "modelFileID": VOLJIN_MODEL},
        "9": {"name": "no model yet", "sex": 2},
    }
    displays = {"10540": 10357, "1256": 3423, "1253": 9999, "-1": 10357, "9": 10357}
    filled = fill_displays(npcs, displays, MODELS.get)
    assert filled == ["10540"]
    assert npcs["10540"]["displayID"] == 10357
    assert npcs["10540"]["displayVia"] == "model"
    assert "displayID" not in npcs["1256"]
    assert npcs["1253"]["displayID"] == 1616  # a display of its own is never replaced
    assert "displayID" not in npcs["-1"] and "displayID" not in npcs["9"]


def test_an_old_classic_json_without_displays_fills_nothing() -> None:
    npcs = {"10540": {"modelFileID": VOLJIN_MODEL, "sex": 2}}
    assert fill_displays(npcs, {}, MODELS.get) == []
    assert "displayID" not in npcs["10540"]


def item(npc: dict) -> Item:
    catalog = VoiceCatalog(load_config().model_copy(update={"voices": Voices()}))
    return Item("gossip", "k", {"npc": "10540", "text": "Yo."}, npc, catalog)


def test_a_filled_display_keeps_the_recast_check() -> None:
    """Its voice was chosen because of the model, so the addon still compares it."""
    filled = {"modelFileID": VOLJIN_MODEL, "displayID": 10357, "displayVia": "model"}
    assert model_cast(item(filled)) == VOLJIN_MODEL
    own = {"modelFileID": VOLJIN_MODEL, "displayID": 10357}
    assert model_cast(item(own)) is None


def test_classicdb_exports_every_creatures_latest_display() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE creature_template "
        "(entry INT, patch INT, name TEXT, display_id1 INT, gossip_menu_id INT)"
    )
    conn.executemany(
        "INSERT INTO creature_template VALUES (?, ?, ?, ?, ?)",
        [
            (10540, 0, "Vol'jin", 10356, 0),
            (10540, 1, "Vol'jin", 10357, 0),  # the later patch wins
            (4949, 0, "Thrall", 4527, 0),
            (1, 0, "no display", 0, 0),
        ],
    )
    creatures = latest_patch_rows(
        conn, "creature_template", "entry", "name, display_id1, gossip_menu_id"
    )
    assert creature_displays(creatures) == {"10540": 10357, "4949": 4527}
