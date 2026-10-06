"""`release_pack --if-changed` counts a file whose audio changed under the same name."""

from __future__ import annotations

import json
from pathlib import Path

from tools.release_pack import changed_files, file_stamp, file_stamps

STATS = {"files": {"842-accept"}, "narratorFiles": set()}
FIRST = {"d": 19.150975, "t": "b982c6e7", "v": "orc-male-guard"}


def stamps(tmp_path: Path, entry: dict) -> dict[str, str]:
    index = tmp_path / "sound_index.json"
    index.write_text(json.dumps({"842-accept": entry}), encoding="utf-8")
    return file_stamps(STATS, index)


def test_a_stamp_is_found_under_the_index_key_for_every_kind_of_file() -> None:
    index = {
        "842-accept": {"d": 19.15, "t": "b982c6e7", "v": "orc-male-guard"},
        "Narrator/orc-male/9000-accept": {"d": 3.0, "t": "aa", "v": "orc-male"},
        "Sex/m/253474-8b1fd97e": {"d": 2.4, "t": "bb", "v": "skyborne-male"},
    }
    assert file_stamp(index, "842-accept") == "b982c6e7:orc-male-guard:19.15"
    assert (
        file_stamp(index, "Quests/Narrator/orc-male/9000-accept") == "aa:orc-male:3.0"
    )
    assert file_stamp(index, "Gossip/Sex/m/253474-8b1fd97e") == "bb:skyborne-male:2.4"
    assert file_stamp(index, "1-accept") == "?"


def test_a_rerolled_take_counts_as_changed(tmp_path: Path) -> None:
    # #333: same text, same voice, a new take, which only its length gives away
    before = stamps(tmp_path, FIRST)
    assert changed_files(before, stamps(tmp_path, {**FIRST, "d": 18.4})) == 1


def test_an_untouched_file_is_no_change(tmp_path: Path) -> None:
    before = stamps(tmp_path, FIRST)
    assert changed_files(before, stamps(tmp_path, dict(FIRST))) == 0


def test_a_text_or_voice_change_still_counts(tmp_path: Path) -> None:
    before = stamps(tmp_path, FIRST)
    assert changed_files(before, stamps(tmp_path, {**FIRST, "t": "00000000"})) == 1
    assert changed_files(before, stamps(tmp_path, {**FIRST, "v": "orc-male"})) == 1


def test_new_and_gone_files_count_and_an_unstamped_release_is_all_change() -> None:
    assert changed_files({"a": "1", "b": "2"}, {"a": "1", "c": "3"}) == 2
    assert changed_files(None, {"a": "1", "b": "2"}) == 2
