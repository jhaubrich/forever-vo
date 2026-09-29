"""`release_pack --if-changed` sees a file whose audio changed under the same name."""

from __future__ import annotations

import json
from pathlib import Path

from tools.release_pack import content_tag

STATS = {"files": {"842-accept"}, "narratorFiles": set()}


def tag(tmp_path: Path, entry: dict) -> str:
    index = tmp_path / "sound_index.json"
    index.write_text(json.dumps({"842-accept": entry}), encoding="utf-8")
    return content_tag(STATS, index)


def test_a_rerolled_take_changes_the_tag(tmp_path: Path) -> None:
    # #333: same text, same voice, a new take, which only its length gives away
    first = {"d": 19.150975, "t": "b982c6e7", "v": "orc-male-guard"}
    again = {**first, "d": 18.4}
    assert tag(tmp_path, first) != tag(tmp_path, again)


def test_an_untouched_file_keeps_its_tag(tmp_path: Path) -> None:
    entry = {"d": 19.150975, "t": "b982c6e7", "v": "orc-male-guard"}
    assert tag(tmp_path, entry) == tag(tmp_path, dict(entry))


def test_a_text_or_voice_change_still_counts(tmp_path: Path) -> None:
    entry = {"d": 19.150975, "t": "b982c6e7", "v": "orc-male-guard"}
    assert tag(tmp_path, entry) != tag(tmp_path, {**entry, "t": "00000000"})
    assert tag(tmp_path, entry) != tag(tmp_path, {**entry, "v": "orc-male"})
