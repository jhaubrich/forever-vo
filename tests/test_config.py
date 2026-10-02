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
    VoiceSources,
    VoiceTuning,
    load_config,
)
from tools.generate import VoiceCatalog
from tools.textkey import text_key


def test_repo_config_loads() -> None:
    config = load_config()
    assert config.voices.narrator == "narrator"
    assert config.voices.narrator_voices[0] == "narrator"
    assert config.tts.voices["dwarf-female"].exaggeration == 0.75
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
    readers = Readers.model_validate(
        {
            "trusted_since": "0.1.4",
            "community": {
                "comment-1": {"player": "It", "class": "Paladin", "restore_name": True}
            },
        }
    )
    assert readers.trusted_since == (0, 1, 4)
    assert readers.trusted_since_text == "0.1.4"
    poster = readers.known(None, "comment-1")
    assert poster is not None and poster.class_ == "Paladin" and poster.restore_name


def test_tuning_follows_the_borrowed_clip(tmp_path: Path) -> None:
    for name in ("npc-3597", "dwarf-male", "dwarf-male-s36", "human-male"):
        (tmp_path / f"{name}.wav").write_bytes(b"")
    config = Config(
        voices=Voices(fallbacks={"darkirondwarf": "dwarf", "narrator": "human-male"}),
        tts=Tts(
            voices={
                "dwarf-male": VoiceTuning(
                    reference="npc-3597", exaggeration=0.75, cfg_weight=0.3
                )
            }
        ),
    )
    catalog = VoiceCatalog(config, voices_dir=tmp_path)

    dwarf = catalog.resolve("dwarf-male")
    assert dwarf.clip == tmp_path / "npc-3597.wav"
    assert (dwarf.settings.exaggeration, dwarf.settings.cfg_weight) == (0.75, 0.3)

    dark_iron = catalog.resolve("darkirondwarf-male")
    assert dark_iron.source == "dwarf-male"
    assert dark_iron.clip == tmp_path / "npc-3597.wav"
    assert dark_iron.settings.exaggeration == 0.75

    # an archetype keeps its own montage and borrows the race's knobs, not the
    # race's reference clip; a missing montage falls through to that clip
    archetype = catalog.resolve("dwarf-male-s36")
    assert archetype.source == "dwarf-male-s36"
    assert archetype.clip == tmp_path / "dwarf-male-s36.wav"
    assert (archetype.settings.exaggeration, archetype.settings.cfg_weight) == (
        0.75,
        0.3,
    )
    assert archetype.settings.reference is None
    assert catalog.tuned("dwarf-male-s36")
    assert catalog.fingerprint("dwarf-male-s36", "Well met.") == (
        text_key("Well met.") + "+cfg_weight=0.3,exaggeration=0.75"
    )
    missing = catalog.resolve("dwarf-male-s99")
    assert missing.source == "dwarf-male"
    assert missing.clip == tmp_path / "npc-3597.wav"
    assert missing.settings.reference == "npc-3597"
    assert missing.settings.exaggeration == 0.75

    own = config.model_copy(
        update={
            "tts": Tts(
                voices={
                    "dwarf-male": VoiceTuning(
                        reference="npc-3597", exaggeration=0.75, cfg_weight=0.3
                    ),
                    "dwarf-male-s36": VoiceTuning(exaggeration=0.2, cfg_weight=0.9),
                }
            )
        }
    )
    kept = VoiceCatalog(own, voices_dir=tmp_path).resolve("dwarf-male-s36")
    assert kept.clip == tmp_path / "dwarf-male-s36.wav"
    assert (kept.settings.exaggeration, kept.settings.cfg_weight) == (0.2, 0.9)

    human = catalog.resolve("human-male")
    assert human.clip == tmp_path / "human-male.wav"
    assert config.tts.is_default(human.settings)

    # a voice on the defaults hashes exactly as before, a tuned one differently, and a
    # knob at its default (tempo) never joins the suffix, so stamped fingerprints hold
    assert catalog.fingerprint("human-male", "Well met.") == text_key("Well met.")
    assert (
        catalog.fingerprint("dwarf-male", "Well met.")
        == text_key("Well met.")
        + "+cfg_weight=0.3,exaggeration=0.75,reference=npc-3597"
    )
    assert catalog.fingerprint(
        "darkirondwarf-male", "Well met."
    ) == catalog.fingerprint("dwarf-male", "Well met.")

    faster = config.model_copy(
        update={"tts": Tts(voices={"human-male": VoiceTuning(tempo=1.1)})}
    )
    assert (
        VoiceCatalog(faster, voices_dir=tmp_path).fingerprint("human-male", "Well met.")
        == text_key("Well met.") + "+tempo=1.1"
    )


def test_recipe_moves_with_what_the_voice_reads_from(tmp_path: Path) -> None:
    """[voices.approved] holds a recipe, so a change to the voice or to one it
    borrows from shows the approval as stale, wherever the edit was made."""
    for name in ("dwarf-male", "human-male"):
        (tmp_path / f"{name}.wav").write_bytes(b"")
    base = load_config().model_copy(
        update={"tts": Tts(), "voices": Voices(fallbacks={"darkirondwarf": "dwarf"})}
    )

    def recipes(config: Config) -> dict[str, str]:
        catalog = VoiceCatalog(config, voices_dir=tmp_path)
        return {
            v: catalog.recipe(v)
            for v in ("dwarf-male", "darkirondwarf-male", "human-male")
        }

    heard = recipes(base)
    assert heard["dwarf-male"] == heard["darkirondwarf-male"] == "clip=dwarf-male"
    assert heard["human-male"] == "clip=human-male"

    picked = base.model_copy(
        update={
            "voices": Voices(
                fallbacks={"darkirondwarf": "dwarf"},
                sources={"dwarf-male": VoiceSources(clips=[1, 2])},
            )
        }
    )
    retuned = base.model_copy(
        update={"tts": Tts(voices={"dwarf-male": VoiceTuning(tempo=1.1)})}
    )
    for changed in (recipes(picked), recipes(retuned)):
        # the borrower moves with the voice it reads from; the others stay approved
        assert changed["dwarf-male"] != heard["dwarf-male"]
        assert changed["darkirondwarf-male"] == changed["dwarf-male"]
        assert changed["human-male"] == heard["human-male"]
    # the same picks again are the same recipe, so an undo brings the approval back
    assert recipes(picked) == recipes(
        picked.model_copy(update={"voices": picked.voices.model_copy()})
    )


def test_pitch_joins_the_fingerprint_only_when_set() -> None:
    from tools.config import Tts, VoiceTuning
    from tools.generate import encode_filters

    tts = Tts(voices={"npc-11658": VoiceTuning(pitch=-4.0)})
    assert "pitch" not in tts.differences(tts.settings_for("human-male"))
    assert tts.differences(tts.settings_for("npc-11658"))["pitch"] == -4.0
    # tempo alone encodes exactly as it did before pitch existed
    assert encode_filters(1.1, 0.0) == ["atempo=1.1"]
    assert encode_filters() == []
    assert encode_filters(1.0, -12.0) == ["rubberband=pitch=0.500000"]


def test_generate_refuses_an_ffmpeg_without_the_pitch_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import generate
    from tools.config import Config, Tts, VoiceTuning

    config = Config(tts=Tts(voices={"npc-11658": VoiceTuning(pitch=-3.0)}))
    assert generate.tuning_filters(config) == ["rubberband=pitch=0.840896"]
    monkeypatch.setattr(generate, "ffmpeg_filters", lambda: frozenset({"atempo"}))
    with pytest.raises(SystemExit, match="rubberband"):
        generate.require_filters(generate.tuning_filters(config))
    generate.require_filters(generate.tuning_filters(Config()))  # nothing asks for it
