"""A legacy player-race model reads as its HD twin, not as no race at all."""

import json
from collections.abc import Iterator

import pytest

from tools import wowdata
from tools.config import DATA_DIR

SCOURGE_MALE, SCOURGE_MALE_HD = 121768, 959310
GOBLIN_MALE = 119376  # no HD twin, and displays of its own


@pytest.fixture
def tables(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Three displays on the HD undead male and one on the goblin, as the client has
    them in miniature: nothing uses the legacy undead file."""
    db2 = {
        "CreatureModelData": {
            1: {"FileDataID": str(SCOURGE_MALE_HD)},
            2: {"FileDataID": str(GOBLIN_MALE)},
        },
        "CreatureDisplayInfo": {
            10: {"ModelID": "1", "ExtendedDisplayInfoID": "100"},
            11: {"ModelID": "1", "ExtendedDisplayInfoID": "101"},
            12: {"ModelID": "1", "ExtendedDisplayInfoID": "102"},
            20: {"ModelID": "2", "ExtendedDisplayInfoID": "200"},
        },
        "CreatureDisplayInfoExtra": {
            100: {"DisplayRaceID": "5", "DisplaySexID": "0"},
            101: {"DisplayRaceID": "5", "DisplaySexID": "0"},
            102: {"DisplayRaceID": "5", "DisplaySexID": "1"},
            200: {"DisplayRaceID": "9", "DisplaySexID": "0"},
        },
    }
    monkeypatch.setattr(wowdata, "load_db2", lambda table: db2[table])
    monkeypatch.setattr(
        wowdata, "_character_model_twins", lambda: {SCOURGE_MALE: SCOURGE_MALE_HD}
    )
    wowdata._race_sex_by_model_file.cache_clear()
    yield
    wowdata._race_sex_by_model_file.cache_clear()


def test_legacy_model_reads_as_its_twin(tables: None) -> None:
    assert wowdata.model_race_sex(SCOURGE_MALE) == (5, 0)
    assert wowdata.model_race_sex(SCOURGE_MALE, 1) == (5, 1)
    assert wowdata.model_race_sex(SCOURGE_MALE) == wowdata.model_race_sex(
        SCOURGE_MALE_HD
    )


def test_a_model_with_displays_keeps_its_own(tables: None) -> None:
    assert wowdata.model_race_sex(GOBLIN_MALE) == (9, 0)


def test_committed_twins_are_player_race_pairs() -> None:
    """The committed map holds the reported speakers' models (#810, #816)."""
    twins = json.loads((DATA_DIR / "character_models.json").read_text())
    assert twins[str(SCOURGE_MALE)] == SCOURGE_MALE_HD
    assert twins["121608"] == 997378  # scourgefemale.m2 -> scourgefemale_hd.m2
    assert str(GOBLIN_MALE) not in twins
