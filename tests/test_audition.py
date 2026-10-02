"""The audition page's edits to forever-vo.toml keep the comments and validate."""

from __future__ import annotations

import random
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import tomlkit
from fastapi import HTTPException

from tools.audition import (
    LineRow,
    random_line,
    run_finished,
    search,
    sound_packs,
    sources_warnings,
    write_approval,
    write_pronunciation,
    write_speaker_voice,
    write_tuning,
    write_voice_sources,
)
from tools.config import CONFIG_TOML, load_config


@pytest.fixture(autouse=True)
def no_desktop_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run's end calls notify-send, which on a desktop pops up for real; a test that
    wants it puts the real function (imported above, so unpatched) back with a
    stand-in for the command."""
    from tools import audition

    monkeypatch.setattr(audition, "run_finished", lambda voice, outcome: None)


@pytest.fixture
def toml_copy(tmp_path: Path) -> Iterator[Path]:
    copy = tmp_path / "forever-vo.toml"
    shutil.copy(CONFIG_TOML, copy)
    yield copy
    load_config.cache_clear()


def test_write_tuning_adds_and_removes_a_voice_and_keeps_comments(
    toml_copy: Path,
) -> None:
    before = toml_copy.read_text(encoding="utf-8")
    config = write_tuning(toml_copy, "gnome-male", 0.6, 0.4, None)
    assert config.tts.voices["gnome-male"].exaggeration == 0.6
    after = toml_copy.read_text(encoding="utf-8")
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in after
    assert "[tts.voices.gnome-male]" in after
    assert config.tts.voices["dwarf-female"].exaggeration == 0.75  # untouched

    # back to the defaults with no clip: the entry goes and the document reads as before
    config = write_tuning(
        toml_copy, "gnome-male", config.tts.exaggeration, config.tts.cfg_weight, None
    )
    assert "gnome-male" not in config.tts.voices
    restored = toml_copy.read_text(encoding="utf-8")
    assert tomlkit.parse(restored).unwrap() == tomlkit.parse(before).unwrap()
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in restored


def test_write_tuning_with_a_reference_and_a_tempo(toml_copy: Path) -> None:
    config = write_tuning(toml_copy, "troll-female", 0.7, 0.35, "npc-1234", tempo=1.1)
    assert config.tts.voices["troll-female"].reference == "npc-1234"
    assert config.tts.voices["troll-female"].tempo == 1.1
    text = toml_copy.read_text(encoding="utf-8")
    assert "[tts.voices.troll-female]" in text and "tempo = 1.1" in text
    # a tempo at the default is left out of the entry
    config = write_tuning(toml_copy, "troll-female", 0.7, 0.35, "npc-1234", tempo=1.0)
    assert config.tts.voices["troll-female"].tempo is None


def test_write_pronunciation_round_trips_and_removes(toml_copy: Path) -> None:
    config = write_pronunciation(toml_copy, "Ironforge", "Iron Forge")
    assert config.pronunciations.respell("To Ironforge!") == "To Iron Forge!"
    assert config.pronunciations.root["Gnomeregan"] == "Nomer-gahn"
    config = write_pronunciation(toml_copy, "Ironforge", "")
    assert "Ironforge" not in config.pronunciations.root


def test_write_speaker_voice_pins_and_unpins_and_keeps_comments(
    toml_copy: Path,
) -> None:
    config = write_speaker_voice(toml_copy, "2991", "tauren-female")
    assert config.voices.speakers["2991"] == "tauren-female"
    assert config.voices.speakers["248200"] == "goblin-male"  # untouched
    text = toml_copy.read_text(encoding="utf-8")
    assert "# One speaker always in one voice" in text
    config = write_speaker_voice(toml_copy, "2991", "")
    assert "2991" not in config.voices.speakers
    # the last entry takes the table with it
    config = write_speaker_voice(toml_copy, "248200", "")
    assert config.voices.speakers == {}
    assert "[voices.speakers]" not in toml_copy.read_text(encoding="utf-8")


def test_write_approval_sets_and_removes_a_recipe_and_keeps_comments(
    toml_copy: Path,
) -> None:
    before = toml_copy.read_text(encoding="utf-8")
    # names no real approval can have, since the repository's file holds real ones
    config = write_approval(toml_copy, "test-male-dark", "clip=test-male-dark")
    config = write_approval(toml_copy, "test-female", "clip=test-female")
    assert config.voices.approved["test-male-dark"] == "clip=test-male-dark"
    assert config.voices.speakers == load_config(CONFIG_TOML).voices.speakers
    # approving again records the recipe heard this time
    config = write_approval(toml_copy, "test-female", "clip=test-female,tempo=1.1")
    assert config.voices.approved["test-female"] == "clip=test-female,tempo=1.1"
    write_approval(toml_copy, "test-female", None)
    config = write_approval(toml_copy, "test-male-dark", None)
    assert "test-female" not in config.voices.approved
    assert "test-male-dark" not in config.voices.approved
    restored = toml_copy.read_text(encoding="utf-8")
    assert tomlkit.parse(restored).unwrap() == tomlkit.parse(before).unwrap()
    # the comments ahead of [voices.approved] and of the table before it stay put
    assert "# Voices someone has auditioned by ear and approved" in restored
    assert restored.index("# CurseForge project IDs.") < restored.index(
        "[release.curseforge_projects]"
    )


def test_write_approval_refuses_what_is_not_a_voice_name(toml_copy: Path) -> None:
    with pytest.raises(HTTPException):
        write_approval(toml_copy, "Scourge Male", "clip=scourge-male")


def test_write_speaker_voice_refuses_what_is_not_a_voice_name(
    toml_copy: Path,
) -> None:
    before = toml_copy.read_text(encoding="utf-8")
    with pytest.raises(HTTPException):
        write_speaker_voice(toml_copy, "2991", "Tauren Female")
    assert toml_copy.read_text(encoding="utf-8") == before


def test_search_needs_every_word_and_ranks_exact_hits_first() -> None:
    rows = [
        LineRow(
            "415-accept",
            "Quests",
            "Rejold's New Brew",
            "Marleth Barleybrew",
            "dwarf-male",
            "raw a",
            "spoken a",
            4,
            "classic",
        ),
        LineRow(
            "415-complete",
            "Quests",
            "Rejold's New Brew",
            "Rejold Barleybrew",
            "dwarf-male",
            "raw b",
            "spoken b",
            4,
            "classic",
        ),
        LineRow(
            "2-accept",
            "Quests",
            "Sharptalon's Claw",
            "Senani Thunderheart",
            "tauren-female",
            "brew of claws",
            "x",
            8,
            "classic",
        ),
    ]
    assert [r.base for r in search(rows, "brew")] == [
        "415-accept",
        "415-complete",
        "2-accept",
    ]
    assert [r.base for r in search(rows, "brew rejold")] == [
        "415-accept",
        "415-complete",
    ]
    assert [r.base for r in search(rows, "415-complete")] == ["415-complete"]
    assert search(rows, "   ") == []


def test_random_line_stays_in_the_voice_and_prefers_quests() -> None:
    rows = [
        LineRow(
            "415-accept",
            "Quests",
            "Rejold's New Brew",
            "Marleth",
            "dwarf-male",
            "a",
            "a",
            4,
            "classic",
        ),
        LineRow(
            "99-1234abcd",
            "Gossip",
            "Marleth",
            "Marleth",
            "dwarf-male",
            "b",
            "b",
            0,
            "classic",
        ),
        LineRow(
            "77-aaaa0000",
            "Gossip",
            "Innkeeper",
            "Innkeeper",
            "gnome-female",
            "c",
            "c",
            0,
            "classic",
        ),
    ]
    rng = random.Random(1)

    def base(voice: str) -> str | None:
        row = random_line(rows, voice, rng)
        return row.base if row else None

    assert {base("dwarf-male") for _ in range(20)} == {
        "415-accept"
    }  # quests first, never the gossip line
    assert (
        base("gnome-female") == "77-aaaa0000"
    )  # gossip when that is all the voice has
    assert base("orc-male") is None


def test_write_voice_sources_adds_and_removes_and_keeps_the_reference_field(
    toml_copy: Path,
) -> None:
    # a voice the real file has no picks for, so the round trip is about this write and
    # not about whatever has been chosen by ear since
    voice = next(
        v
        for v in ("tauren-male", "gnome-male", "orc-female")
        if v not in load_config(toml_copy).voices.sources
    )
    load_config.cache_clear()
    before = toml_copy.read_text(encoding="utf-8")
    config = write_voice_sources(toml_copy, voice, [539282, 539211, 556543])
    assert config.voices.sources[voice].clips == [539282, 539211, 556543]
    text = toml_copy.read_text(encoding="utf-8")
    assert f"[voices.sources.{voice}]" in text
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in text
    # [voices.sources.<v>] is what a clip is made of; [tts.voices.<v>].reference is a
    # different clip to clone from, and writing one must not disturb the other
    assert config.tts.voices == load_config(CONFIG_TOML).tts.voices

    config = write_voice_sources(toml_copy, voice, [])
    assert voice not in config.voices.sources
    assert (
        tomlkit.parse(toml_copy.read_text(encoding="utf-8")).unwrap()
        == tomlkit.parse(before).unwrap()
    )


def test_write_voice_sources_keeps_the_build(toml_copy: Path) -> None:
    config = write_voice_sources(
        toml_copy, "tauren-male", [541910], build="12.1.0.69933"
    )
    assert config.voices.sources["tauren-male"].build == "12.1.0.69933"
    config = write_voice_sources(toml_copy, "tauren-male", [541910])
    assert config.voices.sources["tauren-male"].build is None


def test_sources_warnings_names_a_clip_the_voice_does_not_actually_read(
    toml_copy: Path, tmp_path: Path
) -> None:
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "human-male.wav").touch()
    config = write_tuning(toml_copy, "tauren-male", 0.45, 0.5, "npc-3597")
    # a voice whose tuning clones from elsewhere: picking clips for its own wav is moot
    assert any("npc-3597" in w for w in sources_warnings(config, "tauren-male", voices))
    # human-male is the narrator's clip as well as its own
    assert any("narrator" in w for w in sources_warnings(config, "human-male", voices))


def _pack(addons: Path, folder: str, name: str, priority: int) -> Path:
    (addons / folder / "Data").mkdir(parents=True)
    (addons / folder / "Data" / "Pack.lua").write_text(
        f'{folder}Pack = {{\n    name = "{name}",\n    priority = {priority},\n}}\n',
        encoding="utf-8",
    )
    sounds = addons / folder / "Sounds"
    (sounds / "Quests").mkdir(parents=True)
    return sounds


def test_sound_packs_put_the_working_folder_first_then_follow_the_addon_order(
    tmp_path: Path,
) -> None:
    working = tmp_path / "repo" / "Sounds"
    (working / "Quests").mkdir(parents=True)
    addons = tmp_path / "AddOns"
    _pack(addons, "ForeverVO_Data_Base", "Classic", 100)
    _pack(addons, "ForeverVO_Data_Forever", "Forever", 200)
    (addons / "ForeverVO_Data_Local" / "Data").mkdir(parents=True)
    (addons / "ForeverVO_Data_Local" / "Data" / "Pack.lua").write_text(
        'ForeverVO_DataPack = {\n    name = "Local",\n    priority = 300,\n}\n',
        encoding="utf-8",
    )
    (addons / "ForeverVO_Data_Local" / "Sounds").symlink_to(working)
    (addons / "ForeverVO_Data_Old").mkdir()  # no Sounds: not a pack

    packs = sound_packs(addons, working)

    assert [(p.key, p.label, p.priority) for p in packs] == [
        ("ForeverVO_Data", "working folder", 0),
        ("ForeverVO_Data_Forever", "Forever", 200),
        ("ForeverVO_Data_Base", "Classic", 100),
    ]
    assert packs[0].sounds == working


def test_sound_packs_without_a_client_is_just_the_working_folder(
    tmp_path: Path,
) -> None:
    packs = sound_packs(tmp_path / "missing", tmp_path)
    assert [(p.key, p.label) for p in packs] == [("ForeverVO_Data", "working folder")]


def test_stop_ends_a_run_after_the_take_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import threading

    from fastapi.testclient import TestClient

    from tools import audition

    monkeypatch.setattr(audition, "AUDITION_DIR", tmp_path)
    # the run's end reaches the desktop through notify-send, here a stand-in
    monkeypatch.setattr(audition, "run_finished", run_finished)
    notified: list[tuple[str, ...]] = []
    monkeypatch.setattr(audition.shutil, "which", lambda name: "/bin/notify-send")
    monkeypatch.setattr(
        audition.subprocess,
        "Popen",
        lambda args, **kw: notified.append(("human-male", args[-2], args[-1])),
    )
    # a Studio without its corpus thread or a model: only what /api/generate touches
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_TOML
    studio.model_lock = threading.Lock()
    studio.stops = {}
    client = TestClient(audition.create_app(studio, addons=None))

    class FakeSynth:
        catalog = None

        def render(self, text: str, voice: str) -> str:
            # the page presses Stop while the first take is being made
            (session,) = studio.stops
            assert client.post(f"/api/generate/{session}/stop").json() == {
                "stopping": True
            }
            return "audio"

        def encode(self, audio: str, out: Path, tempo: float, pitch: float) -> float:
            out.write_bytes(b"")
            return 1.0

    monkeypatch.setattr(studio, "synth", lambda: FakeSynth(), raising=False)
    response = client.post(
        "/api/generate",
        json={
            "text": "Hello there.",
            "voice": "human-male",
            "exaggeration": [0.45],
            "cfg_weight": [0.5],
            "takes": 3,
        },
    )
    events = [json.loads(line)["event"] for line in response.text.splitlines()]
    assert events == ["start", "take", "stopped"]
    assert notified == [("human-male", "Audition: human-male", "stopped · 1 take done")]
    assert studio.stops == {}  # a finished run leaves nothing to stop
    assert client.post("/api/generate/nope/stop").json() == {"stopping": False}


def test_one_take_is_encoded_at_every_tempo_and_pitch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model runs once per take; a pitch sweep shifts that same audio."""
    import json
    import threading

    from fastapi.testclient import TestClient

    from tools import audition

    monkeypatch.setattr(audition, "AUDITION_DIR", tmp_path)
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_TOML
    studio.model_lock = threading.Lock()
    studio.stops = {}
    renders: list[str] = []
    encodes: list[tuple[str, float, float]] = []

    class FakeSynth:
        catalog = None

        def render(self, text: str, voice: str) -> str:
            renders.append(text)
            return f"audio{len(renders)}"

        def encode(self, audio: str, out: Path, tempo: float, pitch: float) -> float:
            encodes.append((audio, tempo, pitch))
            out.write_bytes(b"")
            return 1.0

    monkeypatch.setattr(studio, "synth", lambda: FakeSynth(), raising=False)
    client = TestClient(audition.create_app(studio, addons=None))
    response = client.post(
        "/api/generate",
        json={
            "text": "Hello there.",
            "voice": "human-male",
            "exaggeration": [0.45],
            "cfg_weight": [0.5],
            "tempo": [1.0],
            "pitch": [0.0, -3.0, -5.0],
            "takes": 2,
        },
    )
    takes = [json.loads(line) for line in response.text.splitlines()]
    assert len(renders) == 2
    assert encodes == [
        ("audio1", 1.0, 0.0),
        ("audio1", 1.0, -3.0),
        ("audio1", 1.0, -5.0),
        ("audio2", 1.0, 0.0),
        ("audio2", 1.0, -3.0),
        ("audio2", 1.0, -5.0),
    ]
    names = [t["name"] for t in takes if t["event"] == "take"]
    assert names[1] == "human-male-e0.45-c0.5-t1.0-p-3.0-take1.mp3"
    # a past run reads its pitch back from the file name
    (run,) = client.get("/api/sessions").json()
    assert sorted(t["pitch"] for t in run["takes"]) == [
        -5.0,
        -5.0,
        -3.0,
        -3.0,
        0.0,
        0.0,
    ]


def test_write_tuning_keeps_pitch_only_when_it_differs(toml_copy: Path) -> None:
    config = write_tuning(toml_copy, "gnome-male", 0.6, 0.4, None, 1.0, -3.0)
    assert config.tts.voices["gnome-male"].pitch == -3.0
    config = write_tuning(toml_copy, "gnome-male", 0.6, 0.4, None, 1.0, 0.0)
    assert config.tts.voices["gnome-male"].pitch is None


def test_local_clip_borrows_from_the_cache_or_another_voice_and_never_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import audition, refclips

    raw, casc = tmp_path / "raw", tmp_path / "casc"
    monkeypatch.setattr(refclips, "RAW_DIR", raw)
    monkeypatch.setattr(audition, "CASC_DIR", casc)
    monkeypatch.setattr(
        audition,
        "fetch_file",
        lambda fdid, dest, build: _link(casc / build / f"{fdid}.ogg", dest),
    )
    (raw / "orc-male").mkdir(parents=True)
    (raw / "orc-male" / "1.ogg").write_bytes(b"own")
    (casc / "1.0").mkdir(parents=True)
    (casc / "1.0" / "2.ogg").write_bytes(b"cached")
    (raw / "npc-4527").mkdir()
    (raw / "npc-4527" / "3.ogg").write_bytes(b"thrall, fetched before the cache")

    assert audition.local_clip("orc-male", 1, "1.0") == raw / "orc-male" / "1.ogg"
    cached = audition.local_clip("orc-male", 2, "1.0")
    assert cached is not None and cached.read_bytes() == b"cached"
    borrowed = audition.local_clip("orc-male", 3, "1.0")
    assert borrowed == raw / "orc-male" / "3.ogg"
    assert borrowed.read_bytes() == b"thrall, fetched before the cache"
    assert audition.local_clip("orc-male", 4, "1.0") is None  # never downloaded


def _link(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
    return dest
