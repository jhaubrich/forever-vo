"""A quest handed in to more than one NPC (#948): every speaker is kept, the
line's own speaker stays put, and the line is read in each one's voice."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import generate
from tools.config import Readers, load_config
from tools.generate import Item, VoiceCatalog, rebuild_tables, speaker_key_of
from tools.ingest import gather_speakers, merge_entry
from tools.release_pack import file_stamp, pack_files

READERS = Readers(trusted_since=(0, 1, 4))
DOKIMI, MARCY = "256386", "256390"
TEXT = "Thank you for your hard work, $N. Here is your payment."


def reading(npc: str | None, map_id: int | None, time: int, **extra) -> dict:
    return {
        "questID": 91899,
        "event": "complete",
        "text": TEXT,
        "npc": npc,
        "name": {DOKIMI: "Dokimi", MARCY: "Marcy Baker"}.get(npc or "", "Unknown"),
        "mapID": map_id,
        "addon": "0.1.9",
        "time": time,
        **extra,
    }


def test_a_later_reading_from_another_speaker_adds_it_and_keeps_the_first() -> None:
    store: dict = {}
    merge_entry(store, "91899-complete", reading(DOKIMI, 1413, 1), READERS)
    merge_entry(store, "91899-complete", reading(MARCY, 1433, 2), READERS)
    line = store["91899-complete"]
    assert line["speakers"] == {DOKIMI: 1413, MARCY: 1433}
    # The line's own speaker does not flip to whoever played it last
    assert (line["npc"], line["name"], line["mapID"]) == (DOKIMI, "Dokimi", 1413)
    assert line["time"] == 2  # the newer reading still wins the text
    merge_entry(store, "91899-complete", reading(DOKIMI, 1413, 3), READERS)
    assert store["91899-complete"]["speakers"] == {DOKIMI: 1413, MARCY: 1433}


def test_untrusted_and_object_readings_add_no_speaker() -> None:
    store: dict = {}
    merge_entry(store, "91899-complete", reading(DOKIMI, 1413, 1), READERS)
    # Before 0.1.3 a lingering NPC was credited with the line
    merge_entry(
        store, "91899-complete", reading(MARCY, 1433, 2, addon="0.1.2"), READERS
    )
    merge_entry(
        store, "91899-complete", reading("-17184", 1433, 3, isObject=True), READERS
    )
    assert store["91899-complete"]["speakers"] == {DOKIMI: 1413}
    assert store["91899-complete"]["npc"] == DOKIMI


def test_a_winning_reading_with_no_speaker_takes_the_others() -> None:
    store: dict = {}
    merge_entry(
        store, "91899-complete", reading(DOKIMI, 1413, 1, addon="0.1.2"), READERS
    )
    merge_entry(store, "91899-complete", reading(None, None, 2), READERS)
    assert store["91899-complete"].get("npc") is None  # nothing trusted to take
    merge_entry(store, "91899-complete", reading(MARCY, 1433, 1), READERS)
    line = store["91899-complete"]
    assert (line["npc"], line["name"], line["time"]) == (MARCY, "Marcy Baker", 2)


def test_exports_merged_before_speakers_existed_still_count() -> None:
    capture = {"quests": {"91899-complete": reading(DOKIMI, 1413, 1)}}
    gather_speakers(
        capture,
        {"addon": "0.1.8", "quests": {"91899-complete": reading(MARCY, 1433, 2)}},
    )
    gather_speakers(capture, {"quests": {"1-accept": reading(MARCY, 1433, 2)}})
    line = capture["quests"]["91899-complete"]
    assert line["speakers"] == {MARCY: 1433}  # Dokimi's own comes with a merge
    assert line["npc"] == DOKIMI
    assert "1-accept" not in capture["quests"]


VOICES = {DOKIMI: "orc-female", MARCY: "human-female", "1": "orc-female"}


@pytest.fixture
def cast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        generate,
        "voice_for_npc",
        lambda npc, zone, voices, speaker: VOICES[str(speaker)],
    )


def item(speakers: dict[str, int | None]) -> Item:
    entry = {
        **reading(DOKIMI, 1413, 1),
        "speakers": speakers,
    }
    others: dict[str, dict | None] = {key: {"name": f"npc {key}"} for key in speakers}
    return Item(
        "quests",
        "91899-complete",
        entry,
        {"name": "Dokimi"},
        VoiceCatalog(load_config()),
        others,
    )


def test_each_other_speaker_is_read_in_their_own_voice(cast: None) -> None:
    line = item({DOKIMI: 1413, MARCY: 1433, "1": None})
    assert line.voice == "orc-female"
    # Marcy needs a file of her own; speaker 1 sounds like the line already
    assert line.speaker_alternates == {"1": None, MARCY: "human-female"}
    assert item({DOKIMI: 1413}).speaker_alternates == {}


def test_the_tables_list_every_speaker_and_carry_files_that_exist(
    cast: None, tmp_path: Path
) -> None:
    line = item({DOKIMI: 1413, MARCY: 1433, "1": None})
    base = line.variants()[0].base
    sounds = tmp_path / "Sounds"
    (sounds / "Quests").mkdir(parents=True)
    (sounds / "Quests" / f"{base}.mp3").write_bytes(b"")
    index = {base: {"d": 3.0, "v": "orc-female"}}
    data = tmp_path / "Data"
    config = load_config()

    stats = rebuild_tables(
        [line], dict(index), config, data, sounds_dir=sounds, write_index=False
    )
    quests = (data / "Quests.lua").read_text(encoding="utf-8")
    # Marcy is known, her file not made yet: the line's own file plays
    assert "xc={ [1]=true, [256390]=false }" in quests
    assert stats["speakerFiles"] == set()
    npcs = (data / "NPCs.lua").read_text(encoding="utf-8")
    assert "[256386] = 1413," in npcs and "[256390] = 1433," in npcs
    assert '[256390] = "npc 256390",' in npcs

    folder = sounds / "Quests" / "Speaker" / MARCY
    folder.mkdir(parents=True)
    (folder / f"{base}.mp3").write_bytes(b"")
    index[speaker_key_of(base, MARCY)] = {"d": 3.4, "v": "human-female"}
    stats = rebuild_tables(
        [line], dict(index), config, data, sounds_dir=sounds, write_index=False
    )
    quests = (data / "Quests.lua").read_text(encoding="utf-8")
    assert 'xc={ [1]=true, [256390]={ ["d"]=3.400, ["v"]="human-female" } }' in quests
    assert stats["speakerFiles"] == {f"Quests/Speaker/{MARCY}/{base}"}
    assert f"Quests/Speaker/{MARCY}/{base}" in pack_files(stats)
    assert (
        file_stamp(
            {speaker_key_of(base, MARCY): {"t": "aa", "v": "human-female", "d": 3.4}},
            f"Quests/Speaker/{MARCY}/{base}",
        )
        == "aa:human-female:3.4"
    )
