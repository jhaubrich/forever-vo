"""Client data helpers backed by wago.tools (DB2 tables as CSV, files by FileDataID).

No local CASC extraction is needed: wago.tools indexes the wow_classic_beta
builds, so we fetch the handful of tables we need and cache them on disk.
"""
from __future__ import annotations

import csv
import functools
import json
import re
from collections import Counter
from pathlib import Path

import requests

from tools.config import (BETA_BUILD, DATA_DIR, DB2_DIR, GENDER_DICT, RACE_DICT, VOICES_DIR,
                          WAGO_BASE, ZONE_RACE_HINTS)


def db2_path(table: str, build: str = BETA_BUILD) -> Path:
    return DB2_DIR / build / f"{table}.csv"


def fetch_db2(table: str, build: str = BETA_BUILD, force: bool = False) -> Path:
    path = db2_path(table, build)
    if path.exists() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{WAGO_BASE}/db2/{table}/csv"
    response = requests.get(url, params={"build": build}, timeout=180)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path


@functools.lru_cache(maxsize=None)
def load_db2(table: str, build: str = BETA_BUILD) -> dict[int, dict[str, str]]:
    """Returns {ID: row} for a table."""
    path = fetch_db2(table, build)
    with path.open(newline="", encoding="utf-8") as f:
        return {int(row["ID"]): row for row in csv.DictReader(f)}


def fetch_file(fdid: int, dest: Path, build: str = BETA_BUILD) -> Path:
    """Downloads a CASC file (e.g. an .ogg voice line) by FileDataID."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(f"{WAGO_BASE}/api/casc/{fdid}", params={"version": build}, timeout=180)
    response.raise_for_status()
    if response.headers.get("content-type", "").startswith("application/json"):
        raise FileNotFoundError(f"FileDataID {fdid} not in build {build}: {response.text}")
    dest.write_bytes(response.content)
    return dest


def display_race_sex(display_id: int | None) -> tuple[int | None, int | None]:
    """Maps a CreatureDisplayInfo ID to (DisplayRaceID, DisplaySexID).

    The "extra" record carries both, but only player-race models have one. A dryad,
    an ogre or an orphan has none and came back (None, None); with no sex that
    sends the speaker to the narrator, and narrator falls back to human-male.wav.
    CreatureDisplayInfo has a Gender column of its own (0 male, 1 female, 2 none),
    so a creature with no race still gets the right sex: Tarindrella the dryad is
    Gender 1 and was reading in a man's voice.
    """
    if not display_id:
        return None, None
    cdi = load_db2("CreatureDisplayInfo").get(int(display_id))
    if not cdi:
        return None, None
    extra_id = int(cdi.get("ExtendedDisplayInfoID") or 0)
    extra = load_db2("CreatureDisplayInfoExtra").get(extra_id) if extra_id else None
    if extra:
        return int(extra["DisplayRaceID"]), int(extra["DisplaySexID"])
    gender = cdi.get("Gender")
    if gender in ("0", "1"):
        return None, int(gender)
    return None, None


@functools.lru_cache(maxsize=None)
def _species_by_model_file() -> dict[int, str]:
    """{CreatureModelData.FileDataID: species}, from tools/data/species_models.json."""
    path = DATA_DIR / "species_models.json"
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


# A few model folders name a kind of creature the pack has no clip for, while a
# clip it does have is far closer than the adult human they fall back to.
SPECIES_VOICE_ALIASES = {
    "orcmalekid": "humanmalekid-male",
    "orcfemalekid": "humanfemalekid-female",
}


def species_voice_names(species: str, sex_id: int | None) -> list[str]:
    """Voice names to try for a species, most specific first.

    A model folder does not name voices the way the pack does, and three
    mismatches cost real speakers their voice. A folder that already carries the
    sex - nagafemale, titanmale - would be asked for as nagafemale-female and
    never find naga-female.wav, which is why Meridith the Mermaiden spoke as a
    human. A numbered or ghostly variant - satyr2, ogre02, humanmalekid2_ghost -
    misses the clip built for the base model. And a child of another race has no
    child clip of its own: the Orcish Orphan was given a grown man's voice while
    the Human Orphan beside him in the same Children's Week chain was not.
    """
    names: list[str] = []

    def add(name: str) -> None:
        if name and name not in names:
            names.append(name)

    add(SPECIES_VOICE_ALIASES.get(species, ""))
    # A construct is Gender 2 and so has no sex, but an abomination is not
    # genderless the way a player-race speaker with no sex is: the species already
    # says how it sounds. Prefer the recorded sex, then accept either.
    sexes = ([sex_id] if sex_id is not None else []) + [s for s in (0, 1) if s != sex_id]
    stem = species
    while True:
        for s in sexes:
            add(f"{stem}-{GENDER_DICT[s]}")
        carried = re.match(r"^(.+?)(male|female)$", stem)
        if carried:
            add(f"{carried.group(1)}-{carried.group(2)}")
        trimmed = re.sub(r"[0-9]+$", "", re.sub(r"(_ghost|_skeleton)$", "", stem))
        if trimmed == stem or not trimmed:
            return names
        stem = trimmed


def species_voice(display_id: int | None, sex_id: int | None,
                  model_file_id: int | None = None) -> str | None:
    """A voice of the speaker's own kind, where the pack carries one.

    Creatures outside the player races have no DisplayRaceID, so they fall through
    to the zone hint or to human and sound like a person. The model file names the
    species - CreatureModelData.FileDataID points at creature/<species>/<species>.m2 -
    so a clip of that species beats any fallback. Build those with
    build_wc3_references.py (Warcraft III voiced these units properly) or
    build_retail_references.py (retail creature dialogue).

    The display ID leads to that FileDataID through CreatureDisplayInfo and
    CreatureModelData; the captured GetModelFileID() *is* that FileDataID. The
    Forever client never fills the display ID (issue #2), so a speaker the Classic
    export does not know is reached only through the model file (issue #22).
    """
    species = None
    if display_id:
        cdi = load_db2("CreatureDisplayInfo").get(int(display_id))
        model = load_db2("CreatureModelData").get(int((cdi or {}).get("ModelID") or 0))
        species = _species_by_model_file().get(int((model or {}).get("FileDataID") or 0))
    if not species and model_file_id:
        species = _species_by_model_file().get(int(model_file_id))
    if not species:
        return None
    for name in species_voice_names(species, sex_id):
        if (VOICES_DIR / f"{name}.wav").exists():
            return name
    return None


@functools.lru_cache(maxsize=None)
def _race_sex_by_model_file() -> dict[int, Counter]:
    """{CreatureModelData.FileDataID: Counter of (DisplayRaceID, DisplaySexID)} over every
    CreatureDisplayInfo row that uses the model and has an "extra" record.

    Built once: CreatureModelData.FileDataID -> CreatureModelData.ID -> CreatureDisplayInfo.ModelID
    -> CreatureDisplayInfo.ExtendedDisplayInfoID -> CreatureDisplayInfoExtra.
    """
    # Several CreatureModelData rows can share one file, so map by model ID, not by file
    file_by_model = {mid: int(row["FileDataID"] or 0) for mid, row in load_db2("CreatureModelData").items()}
    extras = load_db2("CreatureDisplayInfoExtra")
    result: dict[int, Counter] = {}
    for cdi in load_db2("CreatureDisplayInfo").values():
        fdid = file_by_model.get(int(cdi.get("ModelID") or 0))
        extra = extras.get(int(cdi.get("ExtendedDisplayInfoID") or 0)) if fdid else None
        if extra:
            result.setdefault(fdid, Counter())[(int(extra["DisplayRaceID"]), int(extra["DisplaySexID"]))] += 1
    return result


def model_race_sex(
    model_file_id: int | None, sex_id: int | None = None, preferred_races: set[int] | frozenset[int] = frozenset()
) -> tuple[int | None, int | None]:
    """Maps a captured GetModelFileID() to (DisplayRaceID, DisplaySexID).

    The Forever client never fills GetDisplayInfo() (issue #2), but the model file
    is captured and most models belong to one race. A model shared across races is
    a majority vote over its display rows, narrowed to the captured sex and to
    `preferred_races` (the zone hint, e.g. the Skyborne NPCs on Zephras Isle that
    wear blood elf models) when any row matches; ties break on the lowest race ID.
    """
    if not model_file_id:
        return None, None
    tally = _race_sex_by_model_file().get(int(model_file_id))
    if not tally:
        return None, None
    if sex_id is not None and any(s == sex_id for _, s in tally):
        tally = Counter({k: n for k, n in tally.items() if k[1] == sex_id})
    if any(r in preferred_races for r, _ in tally):
        tally = Counter({k: n for k, n in tally.items() if k[0] in preferred_races})
    (race, sex), _ = min(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    return race, sex


def voice_for_npc(npc: dict | None, zone: str | None = None) -> str:
    """Picks a `race-gender` voice name for a captured NPC record.

    In order: the NPC's own cloned clip (npc-<displayID>.wav), the race and sex of
    its displayID, the same via its modelFileID (the Forever client provides the
    model file but never the display ID; the Classic export supplies display IDs for
    unchanged NPCs), the zone hint, then human. Sex falls back to the in-game UnitSex
    (2 male, 3 female); game objects, items and genderless units go to the narrator.
    """
    if not npc or npc.get("isObject") or npc.get("isObjectOrItem"):
        return "narrator"
    display_id = npc.get("displayID")
    if display_id and (VOICES_DIR / f"npc-{int(display_id)}.wav").exists():
        return f"npc-{int(display_id)}"  # cloned from this NPC's own recorded greetings
    unit_sex = {2: 0, 3: 1}.get(npc.get("sex"))
    hint = ZONE_RACE_HINTS.get(zone or npc.get("zone") or "")
    race_id, sex_id = display_race_sex(display_id)
    if race_id is None:
        hinted = {rid for rid, name in RACE_DICT.items() if name == hint} if hint else set()
        model_race, model_sex = model_race_sex(npc.get("modelFileID"), unit_sex, hinted)
        race_id = model_race
        # Keep a sex the display record gave us. model_race_sex returns a race and
        # a sex together or neither, so assigning its result outright threw away
        # the Gender recovered just above - and with it every female creature's
        # voice, leaving Tarindrella the dryad to be read by a man.
        sex_id = model_sex if model_sex is not None else sex_id
    race = RACE_DICT.get(race_id) if race_id is not None else None
    if sex_id is None:
        sex_id = unit_sex
    if race is None:
        # Before the zone hint or human, see whether this kind of creature has a
        # voice of its own. This also reaches genderless species, which the
        # narrator would otherwise take.
        own = species_voice(display_id, sex_id, npc.get("modelFileID"))
        if own:
            return own
        race = hint or "human"
    if sex_id is None:
        return "narrator"
    return f"{race}-{GENDER_DICT[sex_id]}"


def skyborne_voice_lines() -> list[tuple[int, int, int]]:
    """Returns (RaceID, SoundKitID, FileDataID) for every Skyborne player vocal line.

    These are the only Blizzard-recorded Skyborne voices in the client and serve
    as reference audio for cloning the skyborne-male / skyborne-female voices.
    """
    vocal = load_db2("VocalUISounds")
    entries = load_db2("SoundKitEntry")
    kits_by_race: dict[int, set[int]] = {}
    for row in vocal.values():
        race = int(row["RaceID"])
        if race not in (95, 96):
            continue
        for col, val in row.items():
            if "SoundID" in col and val not in ("", "0"):
                kits_by_race.setdefault(race, set()).add(int(val))
    result = []
    for race, kits in kits_by_race.items():
        for entry in entries.values():
            if int(entry["SoundKitID"]) in kits:
                result.append((race, int(entry["SoundKitID"]), int(entry["FileDataID"])))
    return sorted(set(result))


if __name__ == "__main__":
    lines = skyborne_voice_lines()
    print(f"{len(lines)} Skyborne voice files, e.g. {lines[:5]}")
    print("display 3157 ->", display_race_sex(3157))
    print("model file 997378 ->", model_race_sex(997378), "(scourge; UnitSex male narrows to", model_race_sex(997378, 0), ")")
    print("model file 7478487 ->", model_race_sex(7478487), "(skyborne male)")
    print("model file 1100258 ->", model_race_sex(1100258), "(blood elf model; Zephras Isle hint gives", model_race_sex(1100258, 1, {95, 96}), ")")
