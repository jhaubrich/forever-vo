"""A species alias borrows another kind's clip only when the species has none of its own."""
from __future__ import annotations

from tools.config import Voices
from tools.wowdata import species_voice_names

VOICES = Voices(species_aliases={"orcmalekid": "humanmalekid-male"})


def test_an_alias_comes_after_every_name_of_the_species_itself() -> None:
    names = species_voice_names("orcmalekid", 0, VOICES)
    assert names[-1] == "humanmalekid-male"
    assert names.index("orcmalekid-male") < names.index("humanmalekid-male")


def test_a_species_without_an_alias_is_unchanged() -> None:
    assert species_voice_names("dryad", 1, VOICES) == ["dryad-female", "dryad-male"]
