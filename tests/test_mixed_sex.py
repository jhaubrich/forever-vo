"""A creature ID met as both sexes has its lines in each (#304): which records
count as both, which voice the other sex gets, and what the tables carry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import classicdb, generate
from tools.config import load_config
from tools.generate import Item, VoiceCatalog, known_sexes, rebuild_tables, sex_key
from tools.ingest import gather_sexes, merge_npc, sexes_of

PEACEKEEPER = "253474"


def test_every_sex_seen_is_kept_across_files() -> None:
    npc = merge_npc({}, {"name": "Peacekeeper", "sex": 3}, "a", "0.1.5")
    assert npc["sexes"] == "f"
    npc = merge_npc(npc, {"sex": 2}, "b", "0.1.5")
    assert npc["sexes"] == "mf"
    assert npc["sex"] == 2  # still the last one met, for the line's own file
    assert sexes_of({"sexes": "f"}, {"sex": 3}) == "f"
    assert sexes_of({}, {"sex": 1}) == ""  # UnitSex 1: none


def test_exports_merged_before_sexes_existed_still_count() -> None:
    capture = {"npcs": {PEACEKEEPER: {"name": "Peacekeeper", "sex": 3}}}
    gather_sexes(capture, {"npcs": {PEACEKEEPER: {"sex": 2}, "1": {"sex": 2}}})
    assert capture["npcs"][PEACEKEEPER]["sexes"] == "mf"
    assert "1" not in capture["npcs"]  # only NPCs capture.json already has


def item(npc: dict, speaker: str = PEACEKEEPER, voices_dir: Path | None = None) -> Item:
    config = load_config()
    catalog = VoiceCatalog(config, voices_dir) if voices_dir else VoiceCatalog(config)
    return Item(
        "gossip",
        f"{speaker}|8b1fd97e",
        {"npc": speaker, "text": "What are you looking for, citizen?"},
        npc,
        catalog,
    )


def clips(folder: Path, *voices: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for voice in voices:
        (folder / f"{voice}.wav").write_bytes(b"")
    return folder


def test_the_other_sex_is_the_same_race(tmp_path: Path) -> None:
    voices = clips(tmp_path, "human-male", "skyborne-male", "skyborne-female")
    line = item({"sex": 3, "sexes": "mf"}, voices_dir=voices)
    line.voice = "skyborne-female"
    assert line.sex_alternate == ("m", "skyborne-male")
    guard = item({"sex": 2, "sexes": "mf"}, voices_dir=voices)
    guard.voice = "skyborne-male-guard"  # an archetype's other sex is the plain voice
    assert guard.sex_alternate == ("f", "skyborne-female")


def test_no_other_sex_without_both_or_without_a_clip(tmp_path: Path) -> None:
    voices = clips(tmp_path, "human-male", "skyborne-female")
    one = item({"sex": 3, "sexes": "f"}, voices_dir=voices)
    one.voice = "skyborne-female"
    assert one.sex_alternate is None
    no_clip = item({"sex": 3, "sexes": "mf"}, voices_dir=voices)
    no_clip.voice = "skyborne-female"  # skyborne-male would be read by human-male
    assert no_clip.sex_alternate is None
    named = item({"sex": 2, "sexes": "mf"}, voices_dir=voices)
    named.voice = "npc-3597"
    assert named.sex_alternate is None


def test_a_pinned_speaker_has_one_voice() -> None:
    assert item({"sex": 2, "sexes": "mf"}, speaker="248200").sex_alternate is None


def test_the_tables_carry_the_other_sex_only_when_its_file_exists(
    tmp_path: Path,
) -> None:
    voices = clips(
        tmp_path / "voices", "human-male", "skyborne-male", "skyborne-female"
    )
    line = item({"name": "Peacekeeper", "sex": 3, "sexes": "mf"}, voices_dir=voices)
    line.voice = "skyborne-female"
    base = line.variants()[0].base
    sounds = tmp_path / "Sounds"
    (sounds / "Gossip").mkdir(parents=True)
    (sounds / "Gossip" / f"{base}.mp3").write_bytes(b"")
    index = {base: {"d": 2.0, "v": "skyborne-female"}}
    data = tmp_path / "Data"
    config = load_config()

    stats = rebuild_tables(
        [line], dict(index), config, data, sounds_dir=sounds, write_index=False
    )
    assert stats["sexFiles"] == set()
    assert "s=" not in (data / "Gossip.lua").read_text(encoding="utf-8")

    (sounds / "Gossip" / "Sex" / "m").mkdir(parents=True)
    (sounds / "Gossip" / "Sex" / "m" / f"{base}.mp3").write_bytes(b"")
    index[sex_key(base, "m")] = {"d": 2.4, "v": "skyborne-male"}
    stats = rebuild_tables(
        [line], dict(index), config, data, sounds_dir=sounds, write_index=False
    )
    assert stats["sexFiles"] == {f"Gossip/Sex/m/{base}"}
    assert 's={ ["m"]=2.400 }' in (data / "Gossip.lua").read_text(encoding="utf-8")


RAVENHOLDT_ASSASSIN = "6771"


def test_classic_displays_of_both_sexes_mark_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Ravenholdt Assassin's four displays: two men, two women (#1127)
    sexes = {5907: 0, 5908: 0, 5909: 1, 5910: 1, 100: None}
    monkeypatch.setattr(classicdb, "display_race_sex", lambda d: (1, sexes[d]))
    assert classicdb.display_sexes([5907, 5908, 5909, 5910]) == "mf"
    assert classicdb.display_sexes([5907, 0, 5908, None]) is None
    assert classicdb.display_sexes([5909, 100]) is None  # no sex is not the other


def test_a_capture_does_not_take_back_classic_sexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bulk = tmp_path / "bulk"
    bulk.mkdir()
    classic = {"name": "Ravenholdt Assassin", "displayID": 5907, "sexID": 0}
    (bulk / "classic.json").write_text(
        json.dumps({"npcs": {RAVENHOLDT_ASSASSIN: {**classic, "sexes": "mf"}}}),
        encoding="utf-8",
    )
    capture = tmp_path / "capture.json"
    capture.write_text(
        json.dumps({"npcs": {RAVENHOLDT_ASSASSIN: {"sex": 3, "sexes": "f"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(generate, "DATA_DIR", tmp_path)
    monkeypatch.setattr(generate, "CAPTURE_JSON", capture)
    npc = generate.load_sources()["npcs"][RAVENHOLDT_ASSASSIN]
    assert npc["sexes"] == "mf"
    assert npc["sex"] == 3 and npc["sexID"] == 0


def test_known_sexes_count_the_display_and_the_last_reading() -> None:
    assert known_sexes(None) == ""
    assert known_sexes({"sexID": 0}) == "m"
    assert known_sexes({"sexID": 1, "sex": 2}) == "mf"  # display and unit disagree
    assert known_sexes({"sexes": "f", "sex": 3}) == "f"
    assert known_sexes({"sex": 1}) == ""  # UnitSex 1: none


def test_a_display_and_a_reading_of_the_other_sex_voice_both(tmp_path: Path) -> None:
    voices = clips(tmp_path, "human-male", "human-female")
    line = item({"sexID": 0, "sex": 3}, voices_dir=voices)
    line.voice = "human-male"
    assert line.sex_alternate == ("f", "human-female")


def test_the_pack_lists_every_sex_it_knows_a_speaker_as(tmp_path: Path) -> None:
    voices = clips(tmp_path / "voices", "human-male", "human-female")
    line = item(
        {"name": "Ravenholdt Assassin", "sexID": 0}, RAVENHOLDT_ASSASSIN, voices
    )
    line.voice = "human-male"
    base = line.variants()[0].base
    sounds = tmp_path / "Sounds"
    (sounds / "Gossip").mkdir(parents=True)
    (sounds / "Gossip" / f"{base}.mp3").write_bytes(b"")
    data = tmp_path / "Data"
    rebuild_tables(
        [line],
        {base: {"d": 2.0, "v": "human-male"}},
        load_config(),
        data,
        sounds_dir=sounds,
        write_index=False,
    )
    npcs = (data / "NPCs.lua").read_text(encoding="utf-8")
    # The addon exports the record when it meets a female assassin (Capture.lua)
    assert f'pack.sexes = {{\n\t[{RAVENHOLDT_ASSASSIN}] = "m",\n}}' in npcs
