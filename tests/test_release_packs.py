"""Every line ships in exactly one pack, decided by Classic's snapshot alone, and a
build re-encodes only the files that changed since the pack's last release."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import release_pack
from tools.config import load_config
from tools.generate import Item, VoiceCatalog
from tools.release_pack import PACK_NAMES, Classic, pack_of, pack_specs

CONFIG = load_config()
SPLIT = CONFIG.release.base_split_level
CLASSIC = Classic({842: 12, 5089: 50}, frozenset({"3188|1a2b3c4d"}))


def line(kind: str, key: str, **entry) -> Item:
    return Item(kind, key, entry, {"name": "Someone", "sex": 2}, VoiceCatalog(CONFIG))


def quest(quest_id: int, event: str = "accept", **entry) -> Item:
    return line("quests", f"{quest_id}-{event}", questID=quest_id, event=event, **entry)


def test_classic_quests_split_by_classic_level() -> None:
    assert pack_of(quest(842), CLASSIC, SPLIT) == "classic_quests"
    assert pack_of(quest(5089), CLASSIC, SPLIT) == "classic_endgame"
    # The level is Classic's, whatever an entry says: a capture carries none
    assert pack_of(quest(5089, level=0), CLASSIC, SPLIT) == "classic_endgame"


def test_a_reworded_classic_quest_stays_classic() -> None:
    reworded = quest(842, text="Forever's new words.", source="community")
    assert pack_of(reworded, CLASSIC, SPLIT) == "classic_quests"


def test_what_classic_lacks_is_forever() -> None:
    assert pack_of(quest(65601), CLASSIC, SPLIT) == "forever_quests"
    classic_npc = line("gossip", "3188|1a2b3c4d", npc="3188", text="Hi.")
    new_words = line("gossip", "3188|99999999", npc="3188", text="New.")
    assert pack_of(classic_npc, CLASSIC, SPLIT) == "classic_gossip"
    assert pack_of(new_words, CLASSIC, SPLIT) == "forever_gossip"


def test_books_are_books_whoever_read_them() -> None:
    page = line("books", "5e6f7a8b", event="page", text="A plaque.")
    read = line("books", "5e6f7a8b", event="page", text="A plaque.", player="Jesse")
    assert pack_of(page, CLASSIC, SPLIT) == pack_of(read, CLASSIC, SPLIT) == "books"


def test_every_pack_has_a_spec_and_its_own_folder() -> None:
    specs = pack_specs(CONFIG.release)
    assert set(specs) == set(PACK_NAMES)
    folders = [spec.folder for spec in specs.values()]
    assert len(set(folders)) == len(folders)
    # audition and fetch_packs find packs by this prefix
    assert all(folder.startswith("ForeverVO_Data_") for folder in folders)


def test_a_build_encodes_only_what_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working = tmp_path / "working"
    for name in ("842-accept", "843-accept", "844-accept"):
        (working / "Quests").mkdir(parents=True, exist_ok=True)
        (working / "Quests" / f"{name}.mp3").write_bytes(name.encode())
    encoded: list[str] = []

    def transcode(src: Path, dst: Path, bitrate: str) -> None:
        encoded.append(src.stem)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())

    monkeypatch.setattr(release_pack, "SOUNDS_DIR", working)
    monkeypatch.setattr(release_pack, "RELEASE_DIR", tmp_path)
    monkeypatch.setattr(release_pack, "transcode", transcode)
    now = {"842-accept": "a:v:1", "843-accept": "b:v:2"}
    monkeypatch.setattr(release_pack, "file_stamps", lambda stats: now)
    stats = {"files": set(now), "quests": 2, "gossip": 0}
    stage = tmp_path / "stage"
    gone = stage / "Sounds" / "Quests" / "844-accept.mp3"
    gone.parent.mkdir(parents=True)
    gone.write_bytes(b"old")

    release_pack.package("forever_quests", "1", stage, stats, CONFIG.release)
    assert sorted(encoded) == ["842-accept", "843-accept"]
    assert not gone.exists()  # no longer in the pack, so not in the zip

    encoded.clear()
    last = dict(now)
    now["843-accept"] = "b:v:2.5"  # a new take
    release_pack.package("forever_quests", "2", stage, stats, CONFIG.release, last)
    assert encoded == ["843-accept"]
