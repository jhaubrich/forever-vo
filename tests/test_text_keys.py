"""The ha/hp/hc text keys rebuild_tables writes on a quest record, which the
addon compares with the live text to export a line voiced from other text (#318)."""

from __future__ import annotations

import re
from pathlib import Path

from tools.config import load_config
from tools.generate import VoiceCatalog, load_items, rebuild_tables
from tools.textclean import split_gender
from tools.textkey import text_key


def test_quest_records_carry_the_key_of_the_text_they_were_voiced_from(
    tmp_path: Path,
) -> None:
    config = load_config()
    plain = "You traveled all this way just to help an old woman, $n?"
    branched = "Thank you, $g lad:lass;. Take this, $c."
    capture = {
        "quests": {
            "752-complete": {
                "questID": 752,
                "event": "complete",
                "text": plain,
                "isObject": True,
            },
            "233-accept": {
                "questID": 233,
                "event": "accept",
                "text": branched,
                "isObject": True,
            },
        }
    }
    items = load_items(capture, include_progress=True, catalog=VoiceCatalog(config))
    sounds = tmp_path / "Sounds" / "Quests"
    sounds.mkdir(parents=True)
    index = {}
    for name in ("752-complete", "m-233-accept", "f-233-accept"):
        (sounds / f"{name}.mp3").write_bytes(b"")
        index[name] = {"d": 1.0, "v": "human-male"}
    data = tmp_path / "Data"
    data.mkdir()
    rebuild_tables(
        items,
        index,
        config,
        data_dir=data,
        sounds_dir=tmp_path / "Sounds",
        write_index=False,
    )
    quests = (data / "Quests.lua").read_text(encoding="utf-8")
    assert re.search(rf'hc\s*=\s*"{text_key(plain)}"', quests)
    male, female = (text_key(t) for t in split_gender(branched))
    assert male != female
    assert re.search(rf'ha\s*=\s*"{male},{female}"', quests)
    # the live text a rogue reads keys as the male branch once tokenised
    assert text_key("Thank you, lad. Take this, rogue.", None, "Rogue") == male
