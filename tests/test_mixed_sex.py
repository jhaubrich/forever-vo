"""A creature ID met as both sexes has its lines in each (#304): which records
count as both, which voice the other sex gets, and what the tables carry."""

from __future__ import annotations

from pathlib import Path

from tools.config import load_config
from tools.generate import Item, VoiceCatalog, rebuild_tables, sex_key
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
