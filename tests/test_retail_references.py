"""Combat lines are told apart from speech by whole tokens of the file name."""
from __future__ import annotations

import pytest

from tools.build_retail_references import is_combat


@pytest.mark.parametrize("name", [
    "vo_801_zandalari_guard_attack_01_m.ogg",
    "vo_703_nightborne_female_caster_attack02.ogg",
    "vo_troll_head_hunter_attackcritical03.ogg",
    "vo_troll_head_hunter_woundcritical02.ogg",
    "vo_ogre_wounded_01.ogg",
    "vo_ogre_attacks_01.ogg",
    "vo_ogre_attackctitical_01.ogg",
    "vo_ogre_death_01.ogg",
    "vo_ogre_spellcast02.ogg",
    "vo_ogre_casting_01.ogg",
    "vo_ogre_battleshout_01.ogg",
])
def test_combat_lines_are_rejected(name: str) -> None:
    assert is_combat(name)


@pytest.mark.parametrize("name", [
    "vo_801_zandalari_lower_caste_01_m.ogg",   # "caste" is not "cast"
    "vo_703_nightborne_female_caster_01.ogg",  # "caster" is a body type
    "vo_fleet_admiral_01.ogg",                  # "fleet" is not "flee"
    "vo_deathguard_02.ogg",
    "vo_deathwing_03.ogg",
    "vo_fallen_ranger_04.ogg",
    "vo_spellblade_05.ogg",
])
def test_names_that_begin_like_combat_words_are_kept(name: str) -> None:
    assert not is_combat(name)
