"""A named NPC is offered its whole sound folder, not just its four greetings."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools import refclips, soundpaths
from tools.audition import write_voice_sources
from tools.config import CONFIG_TOML, Voices, load_config
from tools.wowdata import DUD_SIZE, is_dud


def test_folder_entries_keeps_wanted_ogg_lines_only() -> None:
    lines = [
        "561297;sound/creature/sylvanaswindrunner/sylvanaswindrunnergreeting01.ogg",
        "561277;sound/creature/sylvanaswindrunner/wg_sylvanas_hor02.ogg",
        # a sidecar wago answers with a 400
        "2204199;sound/creature/sylvanaswindrunner/sylvanaswindrunnergreeting01.ogg.meta",
        "561218;sound/creature/sylvanas/vo_sylvanas_wickerman_event_09.ogg",
        "600001;sound/creature/Witch Doctor/WitchDoctorYes1.ogg",
        "600002;sound/creature/witch doctor/sub/nested.ogg",
        "600003;sound/spell/sylvanas_aurastart_loop.ogg",
        "not a line",
    ]
    found = soundpaths.folder_entries(lines, {"sylvanaswindrunner", "witch doctor"})
    assert found == {
        "sylvanaswindrunner": [
            (561277, "wg_sylvanas_hor02"),
            (561297, "sylvanaswindrunnergreeting01"),
        ],
        "witch doctor": [(600001, "witchdoctoryes1")],
    }


def test_is_dud_is_empty_zeroed_or_the_placeholder(tmp_path: Path) -> None:
    empty = tmp_path / "empty.ogg"
    empty.write_bytes(b"")
    zeroed = tmp_path / "zeroed.ogg"  # encrypted, served as zero bytes
    zeroed.write_bytes(b"\0" * 29193)
    same_size = tmp_path / "same-size.ogg"  # a real file may have that length
    same_size.write_bytes(b"OggS" + b"\1" * (DUD_SIZE - 4))
    assert is_dud(empty)
    assert is_dud(zeroed)
    assert not is_dud(same_size)


@pytest.fixture
def sylvanas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        refclips, "named_npc_fdids", lambda: {"npc-11657": [561297, 561327]}
    )
    monkeypatch.setattr(refclips, "display_sound_set", lambda d: 175)
    monkeypatch.setattr(refclips, "folders", lambda: {175: "sylvanaswindrunner"})
    monkeypatch.setattr(
        refclips,
        "named_folder_files",
        lambda: {
            "sylvanaswindrunner": [
                (561278, "sylvanas_attackmedium03", 1.4),
                (561280, "sylvanaswindrunneraggro02", 2.0),
                (561286, "hr_sylvanas_hr08", 4.1),
                (561297, "sylvanaswindrunnergreeting01", 5.5),
            ],
            "sylvanas": [(561218, "vo_sylvanas_wickerman_event_09", 6.2)],
        },
    )


def test_named_candidates_kit_then_folder_barks_last(sylvanas: None) -> None:
    found = refclips.named_candidates("npc-11657", Voices())
    assert [(c.group, c.fdid) for c in found] == [
        ("greetings", 561297),
        ("greetings", 561327),
        ("sylvanaswindrunner", 561286),
        ("sylvanaswindrunner", 561297),  # dropped by the fetch pass as a repeat
        ("sylvanaswindrunner", 561278),
        ("sylvanaswindrunner", 561280),
    ]


def test_named_candidates_adds_the_configured_folders(sylvanas: None) -> None:
    voices = Voices(named_folders={"sylvanaswindrunner": ["sylvanas"]})
    found = refclips.named_candidates("npc-11657", voices)
    assert found[-1].group == "sylvanas"
    assert found[-1].kind == "vo_sylvanas_wickerman_event_09"


def test_candidates_fetches_each_fdid_once(
    sylvanas: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched: list[int] = []

    def fetch(self: refclips.Candidate, voice: str) -> bool:
        fetched.append(self.fdid)
        self.seconds = 1.0
        return True

    monkeypatch.setattr(refclips.Candidate, "fetch", fetch)
    found = refclips.candidates("npc-11657", Voices())
    assert [c.fdid for c in found] == [561297, 561327, 561286, 561278, 561280]
    assert fetched == [c.fdid for c in found]
    assert found[0].group == "greetings"


def test_named_folders_keys_are_set_folders() -> None:
    """A mistyped key would silently add nothing."""
    known = set(soundpaths.folders().values())
    for key in load_config().voices.named_folders:
        assert key in known, f"[voices.named_folders] {key} is no set's folder"


@pytest.fixture
def toml_copy(tmp_path: Path) -> Iterator[Path]:
    copy = tmp_path / "forever-vo.toml"
    shutil.copy(CONFIG_TOML, copy)
    yield copy
    load_config.cache_clear()


def test_a_pick_keeps_the_named_folders(toml_copy: Path) -> None:
    text = toml_copy.read_text(encoding="utf-8")
    text = text.replace(
        "[voices.named_folders]\n",
        '[voices.named_folders]\nsylvanaswindrunner = ["sylvanas"]\n',
        1,
    )
    toml_copy.write_text(text, encoding="utf-8")
    config = write_voice_sources(toml_copy, "npc-11657", [561286, 561297])
    assert config.voices.named_folders == {"sylvanaswindrunner": ["sylvanas"]}
    assert config.voices.sources["npc-11657"].clips == [561286, 561297]
