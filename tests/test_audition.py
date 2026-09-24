"""The audition page's edits to forever-vo.toml keep the comments and validate."""
from __future__ import annotations

import random
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import tomlkit

from tools.audition import (
    LineRow,
    random_line,
    search,
    write_pronunciation,
    write_tuning,
)
from tools.config import CONFIG_TOML, load_config


@pytest.fixture
def toml_copy(tmp_path: Path) -> Iterator[Path]:
    copy = tmp_path / "forever-vo.toml"
    shutil.copy(CONFIG_TOML, copy)
    yield copy
    load_config.cache_clear()


def test_write_tuning_adds_and_removes_a_voice_and_keeps_comments(toml_copy: Path) -> None:
    before = toml_copy.read_text(encoding="utf-8")
    config = write_tuning(toml_copy, "gnome-male", 0.6, 0.4, None)
    assert config.tts.voices["gnome-male"].exaggeration == 0.6
    after = toml_copy.read_text(encoding="utf-8")
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in after
    assert "[tts.voices.gnome-male]" in after
    assert config.tts.voices["dwarf-male"].reference == "npc-3597"   # untouched

    # back to the defaults with no clip: the entry goes and the document reads as before
    config = write_tuning(toml_copy, "gnome-male", config.tts.exaggeration, config.tts.cfg_weight, None)
    assert "gnome-male" not in config.tts.voices
    restored = toml_copy.read_text(encoding="utf-8")
    assert tomlkit.parse(restored).unwrap() == tomlkit.parse(before).unwrap()
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in restored


def test_write_tuning_with_a_reference(toml_copy: Path) -> None:
    config = write_tuning(toml_copy, "troll-female", 0.7, 0.35, "npc-1234")
    assert config.tts.voices["troll-female"].reference == "npc-1234"
    assert "[tts.voices.troll-female]" in toml_copy.read_text(encoding="utf-8")


def test_write_pronunciation_round_trips_and_removes(toml_copy: Path) -> None:
    config = write_pronunciation(toml_copy, "Ironforge", "Iron Forge")
    assert config.pronunciations.respell("To Ironforge!") == "To Iron Forge!"
    assert config.pronunciations.root["Gnomeregan"] == "Nomer-gahn"
    config = write_pronunciation(toml_copy, "Ironforge", "")
    assert "Ironforge" not in config.pronunciations.root


def test_search_needs_every_word_and_ranks_exact_hits_first() -> None:
    rows = [
        LineRow("415-accept", "Quests", "Rejold's New Brew", "Marleth Barleybrew", "dwarf-male", "raw a", "spoken a", 4, "classic"),
        LineRow("415-complete", "Quests", "Rejold's New Brew", "Rejold Barleybrew", "dwarf-male", "raw b", "spoken b", 4, "classic"),
        LineRow("2-accept", "Quests", "Sharptalon's Claw", "Senani Thunderheart", "tauren-female", "brew of claws", "x", 8, "classic"),
    ]
    assert [r.base for r in search(rows, "brew")] == ["415-accept", "415-complete", "2-accept"]
    assert [r.base for r in search(rows, "brew rejold")] == ["415-accept", "415-complete"]
    assert [r.base for r in search(rows, "415-complete")] == ["415-complete"]
    assert search(rows, "   ") == []


def test_random_line_stays_in_the_voice_and_prefers_quests() -> None:
    rows = [
        LineRow("415-accept", "Quests", "Rejold's New Brew", "Marleth", "dwarf-male", "a", "a", 4, "classic"),
        LineRow("99-1234abcd", "Gossip", "Marleth", "Marleth", "dwarf-male", "b", "b", 0, "classic"),
        LineRow("77-aaaa0000", "Gossip", "Innkeeper", "Innkeeper", "gnome-female", "c", "c", 0, "classic"),
    ]
    rng = random.Random(1)

    def base(voice: str) -> str | None:
        row = random_line(rows, voice, rng)
        return row.base if row else None

    assert {base("dwarf-male") for _ in range(20)} == {"415-accept"}   # quests first, never the gossip line
    assert base("gnome-female") == "77-aaaa0000"                        # gossip when that is all the voice has
    assert base("orc-male") is None
