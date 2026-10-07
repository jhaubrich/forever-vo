"""A named NPC is offered its whole sound folder, not just its four greetings."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from tools import refclips, soundpaths
from tools.audition import write_voice_sources
from tools.config import Voices, load_config
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
        # a player voice set, under sound/character/
        "1304879;sound/character/pcdhnightelfmale/vo_dhnightelfmaleflirt01.ogg",
        "not a line",
    ]
    found = soundpaths.folder_entries(
        lines, {"sylvanaswindrunner", "witch doctor", "pcdhnightelfmale"}
    )
    assert found == {
        "sylvanaswindrunner": [
            (561277, "wg_sylvanas_hor02"),
            (561297, "sylvanaswindrunnergreeting01"),
        ],
        "witch doctor": [(600001, "witchdoctoryes1")],
        "pcdhnightelfmale": [(1304879, "vo_dhnightelfmaleflirt01")],
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
    # fetched side by side, so in any order, but each FileDataID once
    assert sorted(fetched) == sorted(c.fdid for c in found)
    assert found[0].group == "greetings"


def test_named_folders_keys_are_set_folders() -> None:
    """A mistyped key would silently add nothing."""
    known = set(soundpaths.folders().values())
    for key in load_config().voices.named_folders:
        assert key in known, f"[voices.named_folders] {key} is no set's folder"


def test_a_pick_keeps_the_named_folders(config_copy: Path) -> None:
    voices = config_copy / "voices.toml"
    text = voices.read_text(encoding="utf-8")
    text = text.replace(
        "[voices.named_folders]\n",
        '[voices.named_folders]\nsylvanaswindrunner = ["sylvanas"]\n',
        1,
    )
    voices.write_text(text, encoding="utf-8")
    before = load_config(config_copy).voices.named_folders
    load_config.cache_clear()
    config = write_voice_sources(config_copy, "npc-11657", [561286, 561297])
    assert config.voices.named_folders["sylvanaswindrunner"] == ["sylvanas"]
    assert config.voices.named_folders == before
    assert config.voices.sources["npc-11657"].clips == [561286, 561297]


def test_fetch_file_downloads_once_per_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder probe and every voice's candidates share one download."""
    import requests

    from tools import wowdata

    calls: list[tuple[str, dict]] = []

    class Response:
        headers: ClassVar[dict[str, str]] = {"content-type": "application/octet-stream"}
        content = b"OggS audio"
        text = ""

        def raise_for_status(self) -> None:
            pass

    def get(url: str, params: dict, timeout: int) -> Response:
        calls.append((url, params))
        return Response()

    monkeypatch.setattr(wowdata, "CASC_DIR", tmp_path / "casc")
    monkeypatch.setattr(requests, "get", get)
    first = wowdata.fetch_file(561297, tmp_path / "npc-11657" / "561297.ogg")
    second = wowdata.fetch_file(561297, tmp_path / "npc-15325" / "561297.ogg")
    assert len(calls) == 1
    assert first.read_bytes() == second.read_bytes() == b"OggS audio"
    assert first.stat().st_ino == second.stat().st_ino  # one copy on disk
    first.unlink()  # the probe deletes its own link; the cache keeps the file
    wowdata.fetch_file(561297, tmp_path / "probe" / "561297.ogg")
    assert len(calls) == 1
    # another build is another file
    wowdata.fetch_file(561297, tmp_path / "retail" / "561297.ogg", build="12.1.0.1")
    assert [p["version"] for _, p in calls] == [wowdata.BETA_BUILD, "12.1.0.1"]


def test_fetch_file_caches_nothing_it_could_not_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    from tools import wowdata

    class Missing:
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}
        text = '{"error": "not found"}'

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(wowdata, "CASC_DIR", tmp_path / "casc")
    monkeypatch.setattr(requests, "get", lambda url, params, timeout: Missing())
    with pytest.raises(FileNotFoundError):
        wowdata.fetch_file(1, tmp_path / "v" / "1.ogg")
    assert not (tmp_path / "v" / "1.ogg").exists()
    assert not list((tmp_path / "casc").rglob("*.ogg"))


def test_a_clip_folder_is_a_voice_of_its_own_barks_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        refclips,
        "named_folder_files",
        lambda: {
            "guldan": [
                (1055242, "vo_60_guldan_attack_01", 0.8),
                (1358509, "vo_701_guldan_12", 26.0),
                (1055281, "vo_60_fr_guldan_revealed_02", 11.4),
            ]
        },
    )

    def fetch(self: refclips.Candidate, voice: str) -> bool:
        assert voice == "folder-guldan"  # its own raw folder, like any voice
        self.seconds = 1.0
        return True

    monkeypatch.setattr(refclips.Candidate, "fetch", fetch)
    found = refclips.candidates("folder-guldan", Voices())
    assert [(c.group, c.fdid) for c in found] == [
        ("guldan", 1055281),
        ("guldan", 1358509),
        ("guldan", 1055242),  # the attack bark last
    ]


def test_only_probes_one_folder_and_keeps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    listed = tmp_path / "named_folders.json"
    listed.write_text(
        json.dumps({"thrall": [[562136, "wg_thrall_hor16", 7.02]]}), encoding="utf-8"
    )
    monkeypatch.setattr(soundpaths, "NAMED_FOLDERS", listed)
    monkeypatch.setattr(soundpaths, "named_set_folders", lambda: {1: "thrall"})
    monkeypatch.setattr(
        soundpaths,
        "_listfile_lines",
        lambda: iter(
            [
                "562136;sound/creature/thrall/wg_thrall_hor16.ogg",
                "1358509;sound/creature/guldan/vo_701_guldan_12.ogg",
                "1055242;sound/creature/guldan/vo_60_guldan_attack_01.ogg",
            ]
        ),
    )
    probed: list[int] = []

    def probe(fdid: int, where: Path) -> float | None:
        probed.append(fdid)
        return None if fdid == 1055242 else 26.0  # a dud, left out

    monkeypatch.setattr(soundpaths, "_probe_or_error", probe)
    kept = soundpaths.refresh_folders(Voices(clip_folders=["guldan"]), {"guldan"})
    assert sorted(probed) == [1055242, 1358509]  # Thrall's folder is not probed again
    assert kept == {
        "thrall": [[562136, "wg_thrall_hor16", 7.02]],
        "guldan": [[1358509, "vo_701_guldan_12", 26.0]],
    }
    assert json.loads(listed.read_text(encoding="utf-8")) == kept


def test_clip_folders_must_be_spelled_as_the_listfile_keys_them() -> None:
    assert Voices(
        clip_folders=["guldan", "image_of_guldan", "pc_-_nightborne_elf_male"]
    ).clip_folders
    with pytest.raises(ValueError):
        Voices(clip_folders=["Guldan"])


def test_other_sex_set_catches_the_other_sex_and_not_another_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sets = {
        52: "nightelffemalesentinelnpc",
        59: "nightelfmalestandardnpc",
        149: "nightelfmalestandardnpc",
        82: "undeadfemalestandardnpc",
        174: "fandralstaghelm",
    }
    monkeypatch.setattr(soundpaths, "folders", lambda: sets)
    assert soundpaths.other_sex_set(52, "nightelf-male")
    assert not soundpaths.other_sex_set(52, "nightelf-female")
    assert not soundpaths.other_sex_set(59, "nightelf-male")
    # blood elves were cast with night elf sets; those recordings are their voice
    assert not soundpaths.other_sex_set(149, "bloodelf-male")
    # undead is how Blizzard files the scourge
    assert soundpaths.other_sex_set(82, "scourge-male")
    assert not soundpaths.other_sex_set(174, "nightelf-female")
    assert not soundpaths.other_sex_set(999, "nightelf-male")  # no folder known
