"""[voices.speakers] names a speaker's voice outright, ahead of anything captured."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tools.config import Voices, load_config
from tools.wowdata import voice_for_npc

VOICES = Voices(speakers={"248200": "goblin-male"})
# Fizzlefuse's record as the capture left it: an orc model on a goblin (#352)
FIZZLEFUSE = {"creatureType": "Humanoid", "modelFileID": 917116, "sex": 2}


def test_a_listed_speaker_takes_its_voice_whatever_the_record_says() -> None:
    assert voice_for_npc(FIZZLEFUSE, voices=VOICES, speaker="248200") == "goblin-male"


def test_an_unlisted_speaker_resolves_as_before() -> None:
    npc = {"isObject": True}
    assert voice_for_npc(npc, voices=VOICES, speaker="1") == VOICES.narrator
    assert voice_for_npc(npc, voices=VOICES) == VOICES.narrator


def test_the_repository_file_lists_fizzlefuse() -> None:
    assert load_config().voices.speakers["248200"] == "goblin-male"


@pytest.mark.parametrize(
    "speakers",
    [{"Fizzlefuse": "goblin-male"}, {"0": "goblin-male"}, {"248200": "Goblin Male"}],
)
def test_a_key_or_voice_of_the_wrong_shape_is_refused(speakers: dict) -> None:
    with pytest.raises(ValidationError):
        Voices(speakers=speakers)


def test_a_game_object_and_an_archetype_are_accepted() -> None:
    voices = Voices(speakers={"-1234": "narrator", "2991": "tauren-female-young"})
    assert voices.speakers["-1234"] == "narrator"
