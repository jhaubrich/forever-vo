"""Exports every Classic quest and gossip line from the VMaNGOS database snapshot
into tools/data/bulk/classic.json (same schema as capture.json).

WoW Forever reuses Classic's quest IDs and, for most quests, the exact text, so
this is the bulk seed for a voice pack. Lines captured in game override it
(generate.py merges sources with capture > questcache > classic).

    ./tools/run.sh tools/classicdb.py            # downloads the snapshot if needed

Snapshot: https://github.com/vmangos/core/releases/tag/db_latest (SQLite build).
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Any

import requests

from tools.config import DATA_DIR
from tools.textkey import text_key
from tools.wowdata import display_race_sex

CLASSICDB_DIR = DATA_DIR / "classicdb"
BULK_DIR = DATA_DIR / "bulk"
OUTPUT = BULK_DIR / "classic.json"
RELEASE_API = "https://api.github.com/repos/vmangos/core/releases/tags/db_latest"


def ensure_snapshot() -> Path:
    existing = list(CLASSICDB_DIR.glob("**/mangos.sqlite"))
    if existing:
        return existing[0]
    CLASSICDB_DIR.mkdir(parents=True, exist_ok=True)
    release = requests.get(RELEASE_API, timeout=60).json()
    asset = next(a for a in release["assets"] if a["name"].startswith("db-sqlite"))
    print(f"downloading {asset['name']} ({asset['size'] / 1e6:.0f} MB)")
    data = requests.get(asset["browser_download_url"], timeout=600).content
    zipfile.ZipFile(io.BytesIO(data)).extractall(CLASSICDB_DIR)
    return next(CLASSICDB_DIR.glob("**/mangos.sqlite"))


def latest_patch_rows(conn: sqlite3.Connection, table: str, key: str, columns: str):
    """VMaNGOS keeps one row per patch; keep the newest patch for each entry."""
    rows = conn.execute(f"SELECT {key}, patch, {columns} FROM {table} ORDER BY {key}, patch").fetchall()
    latest = {}
    for row in rows:
        latest[row[0]] = row  # rows are ordered by patch ascending, so the last one wins
    return latest


def collect_gossip_menus(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """menu id -> set of npc_text ids reachable through that menu and its sub-menus."""
    texts_by_menu: dict[int, set[int]] = {}
    for menu, text_id in conn.execute("SELECT entry, text_id FROM gossip_menu"):
        texts_by_menu.setdefault(menu, set()).add(text_id)
    children: dict[int, set[int]] = {}
    for menu, action in conn.execute("SELECT menu_id, action_menu_id FROM gossip_menu_option WHERE action_menu_id > 0"):
        children.setdefault(menu, set()).add(action)

    resolved: dict[int, set[int]] = {}

    def walk(menu: int, seen: set[int]) -> set[int]:
        if menu in resolved:
            return resolved[menu]
        seen.add(menu)
        texts = set(texts_by_menu.get(menu, ()))
        for child in children.get(menu, ()):
            if child not in seen:
                texts |= walk(child, seen)
        resolved[menu] = texts
        return texts

    for menu in list(texts_by_menu):
        walk(menu, set())
    return resolved


def main() -> int:
    db_path = ensure_snapshot()
    conn = sqlite3.connect(db_path)

    quests = latest_patch_rows(conn, "quest_template", "entry",
                               "QuestLevel, Title, Details, Objectives, RequestItemsText, OfferRewardText")
    creatures = latest_patch_rows(conn, "creature_template", "entry", "name, display_id1, gossip_menu_id")
    objects = latest_patch_rows(conn, "gameobject_template", "entry", "name")
    items = latest_patch_rows(conn, "item_template", "entry", "name, start_quest")

    def relations(table: str) -> dict[int, int]:
        result: dict[int, int] = {}
        for giver, quest in conn.execute(f"SELECT id, quest FROM {table} ORDER BY patch_max"):
            result[quest] = giver
        return result

    accept_by_creature = relations("creature_questrelation")
    complete_by_creature = relations("creature_involvedrelation")
    accept_by_object = relations("gameobject_questrelation")
    complete_by_object = relations("gameobject_involvedrelation")
    accept_by_item = {row[3]: row[0] for row in items.values() if row[3]}

    out: dict[str, Any] = {"version": 2, "source": "classic", "quests": {}, "gossip": {}, "npcs": {}}
    npcs: dict[str, dict] = out["npcs"]

    def creature_speaker(entry: int) -> str | None:
        row = creatures.get(entry)
        if not row:
            return None
        key = str(entry)
        if key not in npcs:
            race, sex = display_race_sex(row[3])
            npcs[key] = {"name": row[2], "displayID": row[3], "raceID": race, "sexID": sex}
        return key

    def object_speaker(entry: int) -> str | None:
        row = objects.get(entry)
        if not row:
            return None
        key = str(-entry)
        npcs.setdefault(key, {"name": row[2], "isObject": True})
        return key

    def add_quest(quest_id: int, event: str, text: str | None, giver_key: str | None, giver_name: str | None,
                  title: str, level: int, is_object: bool):
        if not text or not text.strip():
            return
        out["quests"][f"{quest_id}-{event}"] = {
            "event": event, "questID": quest_id, "title": title, "text": text,
            "npc": giver_key, "name": giver_name, "isObject": is_object or None,
            "level": level, "source": "classic",
        }

    for quest_id, row in quests.items():
        _, _, level, title, details, _objectives, request_items, offer_reward = row
        # accept: creature, else game object, else item (narrator)
        giver_key = giver_name = None
        is_object = False
        if quest_id in accept_by_creature:
            giver_key = creature_speaker(accept_by_creature[quest_id])
            giver_name = giver_key and npcs[giver_key]["name"]
        elif quest_id in accept_by_object:
            giver_key = object_speaker(accept_by_object[quest_id])
            giver_name = giver_key and npcs[giver_key]["name"]
            is_object = True
        elif quest_id in accept_by_item:
            giver_name = items[accept_by_item[quest_id]][2]
            is_object = True
        add_quest(quest_id, "accept", details, giver_key, giver_name, title, level, is_object)

        ender_key = ender_name = None
        is_object = False
        if quest_id in complete_by_creature:
            ender_key = creature_speaker(complete_by_creature[quest_id])
            ender_name = ender_key and npcs[ender_key]["name"]
        elif quest_id in complete_by_object:
            ender_key = object_speaker(complete_by_object[quest_id])
            ender_name = ender_key and npcs[ender_key]["name"]
            is_object = True
        add_quest(quest_id, "complete", offer_reward, ender_key, ender_name, title, level, is_object)
        add_quest(quest_id, "progress", request_items, ender_key, ender_name, title, level, is_object)

    # Gossip: creature -> menu -> npc_text -> broadcast_text (male/female by the model's sex)
    menus = collect_gossip_menus(conn)
    npc_texts = {row[0]: row[1:] for row in conn.execute(
        "SELECT ID, BroadcastTextID0, BroadcastTextID1, BroadcastTextID2, BroadcastTextID3, "
        "BroadcastTextID4, BroadcastTextID5, BroadcastTextID6, BroadcastTextID7 FROM npc_text")}
    broadcast = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT entry, male_text, female_text FROM broadcast_text")}

    gossip_count = 0
    for entry, row in creatures.items():
        menu = row[4]
        if not menu or menu not in menus:
            continue
        key = creature_speaker(entry)
        if not key:
            continue
        sex = npcs[key].get("sexID")
        for text_id in menus[menu]:
            for bt_id in npc_texts.get(text_id, ()):
                if not bt_id:
                    continue
                male, female = broadcast.get(bt_id, (None, None))
                text = (female if sex == 1 and female else male) or female
                if not text or not text.strip():
                    continue
                gossip_key = f"{key}|{text_key(text)}"
                if gossip_key in out["gossip"]:
                    continue
                out["gossip"][gossip_key] = {
                    "event": "gossip", "text": text, "npc": key, "name": row[2], "source": "classic",
                }
                gossip_count += 1

    BULK_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    accept = sum(1 for k in out["quests"] if k.endswith("-accept"))
    complete = sum(1 for k in out["quests"] if k.endswith("-complete"))
    progress = sum(1 for k in out["quests"] if k.endswith("-progress"))
    print(f"{OUTPUT}: {len(quests)} quests -> {accept} accept, {complete} complete, {progress} progress texts; "
          f"{gossip_count} gossip lines; {len(npcs)} speakers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
