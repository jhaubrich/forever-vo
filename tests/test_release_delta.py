"""The delta pack is what the Base packs do not already ship as it is."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import release_pack
from tools.config import load_config
from tools.generate import Item, VoiceCatalog
from tools.release_pack import delta_line, file_stamp, line_files, load_baseline

CONFIG = load_config()
INDEX = {
    "842-accept": {"d": 19.15, "t": "b982c6e7", "v": "orc-male-guard"},
    "Narrator/orc-male/9000-accept": {"d": 3.0, "t": "aa", "v": "orc-male"},
    "Sex/m/253474-8b1fd97e": {"d": 2.4, "t": "bb", "v": "skyborne-male"},
}


def test_a_stamp_is_found_under_the_index_key_for_every_kind_of_file() -> None:
    assert file_stamp(INDEX, "842-accept") == "b982c6e7:orc-male-guard:19.15"
    assert (
        file_stamp(INDEX, "Quests/Narrator/orc-male/9000-accept") == "aa:orc-male:3.0"
    )
    assert file_stamp(INDEX, "Gossip/Sex/m/253474-8b1fd97e") == "bb:skyborne-male:2.4"
    assert file_stamp(INDEX, "1-accept") == "?"


def quest(key: str = "842-accept", **entry) -> Item:
    quest_id, _, event = key.partition("-")
    return Item(
        "quests",
        key,
        {"questID": int(quest_id), "event": event, "text": "Go north.", **entry},
        {"name": "Kargal Battlescar", "sex": 2},
        VoiceCatalog(CONFIG),
    )


def sounds(tmp_path: Path, *names: str) -> Path:
    for name in names:
        path = tmp_path / ("Quests" if "/" not in name else "") / f"{name}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return tmp_path


def test_a_line_the_base_ships_as_it_is_stays_out(tmp_path: Path) -> None:
    line = quest()
    folder = sounds(tmp_path, "842-accept")
    baseline = {"842-accept": file_stamp(INDEX, "842-accept")}
    assert not delta_line(line, CONFIG, baseline, INDEX, folder)


def test_a_new_take_a_new_line_or_a_lost_file_goes_in(tmp_path: Path) -> None:
    line = quest()
    folder = sounds(tmp_path, "842-accept")
    old = {"842-accept": "b982c6e7:orc-male-guard:20.3"}  # before the re-roll
    assert delta_line(line, CONFIG, old, INDEX, folder)
    assert delta_line(line, CONFIG, {}, INDEX, folder)  # new since the Base build
    gone = quest("843-accept")  # shipped, and no file for it now
    assert delta_line(gone, CONFIG, {"843-accept": "x:y:1"}, INDEX, folder)


def test_a_file_neither_shipped_nor_made_is_no_change(tmp_path: Path) -> None:
    line = quest()
    folder = sounds(tmp_path, "842-accept")
    names = line_files(line, CONFIG)
    baseline = {"842-accept": file_stamp(INDEX, "842-accept")}
    # whatever else the line could have, none of it exists or shipped
    assert set(names) - {"842-accept"} == set(names) - set(baseline)
    assert not delta_line(line, CONFIG, baseline, INDEX, folder)


def test_the_baseline_needs_both_base_packs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "release_baseline.json"
    monkeypatch.setattr(release_pack, "BASELINE_FILE", path)
    assert load_baseline() is None
    path.write_text(json.dumps({"base": {"842-accept": "s"}}), encoding="utf-8")
    assert load_baseline() is None  # the other's whole set would land in the delta
    path.write_text(
        json.dumps({"base": {"842-accept": "s"}, "base_endgame": {"9000-accept": "t"}}),
        encoding="utf-8",
    )
    assert load_baseline() == {"842-accept": "s", "9000-accept": "t"}
