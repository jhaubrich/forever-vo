"""forever-vo.toml loads, and the models resolve the way the pipeline relies on."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.config import (
    Config,
    ConfigError,
    Pronunciations,
    Readers,
    Tts,
    Voices,
    VoiceTuning,
    load_config,
)
from tools.generate import VoiceCatalog
from tools.textkey import text_key


def test_repo_config_loads() -> None:
    config = load_config()
    assert config.voices.narrator == "narrator"
    assert config.voices.narrator_voices[0] == "narrator"
    assert config.tts.voices["dwarf-male"].reference == "npc-3597"
    assert config.readers.trusted_since == (0, 1, 4)
    assert config.release.curseforge_projects["addon"] == 1705010


def test_unknown_key_is_an_error(tmp_path: Path) -> None:
    bad = tmp_path / "forever-vo.toml"
    bad.write_text("[tts]\nexageration = 0.5\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_pronunciations_respell_whole_words_and_keep_shouting() -> None:
    spoken = Pronunciations({"Gnomeregan": "Nomer-gahn"})
    assert spoken.respell("Go to Gnomeregan's gate.") == "Go to Nomer-gahn's gate."
    assert spoken.respell("GNOMEREGAN!") == "NOMER-GAHN!"
    assert spoken.respell("Gnomereganite") == "Gnomereganite"
    assert Pronunciations({}).respell("unchanged") == "unchanged"


def test_readers_parse_the_version_and_know_the_poster() -> None:
    readers = Readers.model_validate({
        "trusted_since": "0.1.4",
        "community": {"comment-1": {"player": "It", "class": "Paladin", "restore_name": True}},
    })
    assert readers.trusted_since == (0, 1, 4)
    assert readers.trusted_since_text == "0.1.4"
    poster = readers.known(None, "comment-1")
    assert poster is not None and poster.class_ == "Paladin" and poster.restore_name


def test_tuning_follows_the_borrowed_clip(tmp_path: Path) -> None:
    for name in ("npc-3597", "dwarf-male", "human-male"):
        (tmp_path / f"{name}.wav").write_bytes(b"")
    config = Config(
        voices=Voices(fallbacks={"darkirondwarf": "dwarf", "narrator": "human-male"}),
        tts=Tts(voices={"dwarf-male": VoiceTuning(reference="npc-3597", exaggeration=0.75, cfg_weight=0.3)}),
    )
    catalog = VoiceCatalog(config, voices_dir=tmp_path)

    dwarf = catalog.resolve("dwarf-male")
    assert dwarf.clip == tmp_path / "npc-3597.wav"
    assert (dwarf.settings.exaggeration, dwarf.settings.cfg_weight) == (0.75, 0.3)

    dark_iron = catalog.resolve("darkirondwarf-male")
    assert dark_iron.source == "dwarf-male"
    assert dark_iron.clip == tmp_path / "npc-3597.wav"
    assert dark_iron.settings.exaggeration == 0.75

    human = catalog.resolve("human-male")
    assert human.clip == tmp_path / "human-male.wav"
    assert config.tts.is_default(human.settings)

    # a voice on the defaults hashes exactly as before, a tuned one differently
    assert catalog.fingerprint("human-male", "Well met.") == text_key("Well met.")
    assert catalog.fingerprint("dwarf-male", "Well met.") == text_key("Well met.") + "+cfg_weight=0.3,exaggeration=0.75,reference=npc-3597"
    assert catalog.fingerprint("darkirondwarf-male", "Well met.") == catalog.fingerprint("dwarf-male", "Well met.")
