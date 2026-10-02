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


def test_a_raceless_display_is_cast_by_its_sound_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import wowdata

    # Gryfe as the client tables have him: no race on the display, set 66 its barks
    monkeypatch.setattr(wowdata, "display_race_sex", lambda display: (None, 0))
    monkeypatch.setattr(wowdata, "display_sound_set", lambda display: 66)
    monkeypatch.setattr(wowdata, "species_voice", lambda *a, **kw: None)
    monkeypatch.setattr(wowdata, "model_race_sex", lambda *a: (None, None))
    gryfe = {"displayID": 1233, "sex": 2}
    voices = Voices(sound_sets={"66": "goblin-male-zany"})
    assert voice_for_npc(gryfe, voices=voices, speaker="10583") == "goblin-male-zany"
    assert voice_for_npc(gryfe, voices=Voices(), speaker="10583") == "human-male"
    # a species clip, when there is one, still comes first
    monkeypatch.setattr(wowdata, "species_voice", lambda *a, **kw: "banshee-female")
    assert voice_for_npc(gryfe, voices=voices, speaker="10583") == "banshee-female"


def test_sound_sets_are_keyed_by_set_id_and_name_a_voice() -> None:
    with pytest.raises(ValidationError):
        Voices(sound_sets={"set66": "goblin-male"})
    with pytest.raises(ValidationError):
        Voices(sound_sets={"66": "Goblin Male"})
