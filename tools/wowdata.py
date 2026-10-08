"""Client data helpers backed by wago.tools (DB2 tables as CSV, files by FileDataID).

No local CASC extraction is needed: wago.tools indexes the wow_classic_beta
builds, so we fetch the handful of tables we need and cache them on disk.
"""

from __future__ import annotations

import csv
import functools
import hashlib
import json
import os
import re
import shutil
import threading
from collections import Counter
from pathlib import Path

import requests

from tools.config import (
    BETA_BUILD,
    CASC_DIR,
    DATA_DIR,
    DB2_DIR,
    GENDER_DICT,
    RACE_DICT,
    VOICES_DIR,
    WAGO_BASE,
    Voices,
    load_config,
)


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


@functools.cache
def load_db2(table: str, build: str = BETA_BUILD) -> dict[int, dict[str, str]]:
    """Returns {ID: row} for a table."""
    path = fetch_db2(table, build)
    with path.open(newline="", encoding="utf-8") as f:
        return {int(row["ID"]): row for row in csv.DictReader(f)}


def fetch_file(fdid: int, dest: Path, build: str = BETA_BUILD) -> Path:
    """Puts a CASC file (e.g. an .ogg voice line) at `dest`, by FileDataID.

    Each file is downloaded once per build into CASC_DIR/<build>/ and hard-linked to
    wherever it is asked for: a file's bytes never change within a build, and the same
    clip is wanted in several places - `fvo-soundpaths --folders` probes every line of
    the named folders, then the audition fetches them again per voice into
    tools/voices/raw/<voice>/, once for each display of a character (Tyrande's two
    folders are ~500 files). A link costs no second copy; a copy is the fallback where
    the two directories cannot share one.
    """
    if dest.exists():
        return dest
    cached = CASC_DIR / build / f"{fdid}{dest.suffix}"
    if not cached.exists():
        _download(fdid, cached, build)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(cached, dest)
    except FileExistsError:
        pass  # another thread put it there first
    except OSError:
        part = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.part")
        try:
            shutil.copyfile(cached, part)
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)
    return dest


def _download(fdid: int, dest: Path, build: str) -> None:
    """Written to a temporary file and renamed. The cache is "the file is there", so a
    download cut short by a dropped connection or a Ctrl-C used to be kept for good, and
    every later reference built from that clip was silently truncated: ffmpeg's concat
    demuxer stops at the first input it cannot open and still exits 0.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        f"{WAGO_BASE}/api/casc/{fdid}", params={"version": build}, timeout=180
    )
    response.raise_for_status()
    if response.headers.get("content-type", "").startswith("application/json"):
        raise FileNotFoundError(
            f"FileDataID {fdid} not in build {build}: {response.text}"
        )
    # the thread too: the folder probe in soundpaths fetches from a pool in one process
    part = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        part.write_bytes(response.content)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


# The shapes wago serves for a FileDataID the listfile names but this build cannot
# play: an empty file (every vo_1127_* line); the same length of zero bytes where the
# file is encrypted with a key wago does not have (vo_111_gazlowe_*); and one 3,447-byte,
# 0.0003 s Ogg that is byte-identical wherever it stands in (later-expansion lines of
# Sylvanas and Thrall). Tested by content, not by length: real barks run to 0.1 s.
DUD_SIZE = 3447
DUD_MD5 = "de6135861a6cacfe176830f18f597c3e"


def is_dud(path: Path) -> bool:
    """True for a file that stands in for audio this client does not have."""
    data = path.read_bytes()
    if not data.strip(b"\0"):  # empty, or zeroed
        return True
    return len(data) == DUD_SIZE and hashlib.md5(data).hexdigest() == DUD_MD5


def display_model_file(display_id: int | None) -> int | None:
    """The model file a creature display draws, which is what a capture records
    (`modelFileID`, from GetModelFileID): CreatureDisplayInfo.ModelID ->
    CreatureModelData.FileDataID."""
    if not display_id:
        return None
    row = load_db2("CreatureDisplayInfo").get(int(display_id))
    if not row:
        return None
    model = load_db2("CreatureModelData").get(int(row.get("ModelID") or 0))
    if not model:
        return None
    return int(model.get("FileDataID") or 0) or None


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


@functools.cache
def _species_by_model_file() -> dict[int, str]:
    """{CreatureModelData.FileDataID: species}, from tools/data/species_models.json."""
    path = DATA_DIR / "species_models.json"
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def species_voice_names(species: str, sex_id: int | None, voices: Voices) -> list[str]:
    """Voice names to try for a species, most specific first.

    A model folder does not name voices the way the pack does, and three
    mismatches cost real speakers their voice. A folder that already carries the
    sex - nagafemale, titanmale - would be asked for as nagafemale-female and
    never find naga-female.wav, which is why Meridith the Mermaiden spoke as a
    human. A numbered or ghostly variant - satyr2, ogre02, humanmalekid2_ghost -
    misses the clip built for the base model. And a child of another race has no
    child clip of its own ([voices.species_aliases]): the Orcish Orphan was given
    a grown man's voice while the Human Orphan beside him in the same Children's
    Week chain was not.
    """
    names: list[str] = []

    def add(name: str) -> None:
        if name and name not in names:
            names.append(name)

    # A construct is Gender 2 and so has no sex, but an abomination is not
    # genderless the way a player-race speaker with no sex is: the species already
    # says how it sounds. Prefer the recorded sex, then accept either.
    sexes = ([sex_id] if sex_id is not None else []) + [
        s for s in (0, 1) if s != sex_id
    ]
    stem = species
    while True:
        for s in sexes:
            add(f"{stem}-{GENDER_DICT[s]}")
        carried = re.match(r"^(.+?)(male|female)$", stem)
        if carried:
            add(f"{carried.group(1)}-{carried.group(2)}")
        trimmed = re.sub(r"[0-9]+$", "", re.sub(r"(_ghost|_skeleton)$", "", stem))
        if trimmed == stem or not trimmed:
            # Last, not first: an alias lends a species someone else's clip, so it
            # must never outrank a clip of the speaker's own kind. First, it would
            # keep the Orcish Orphan on the Kul Tiran child even after an
            # orcmalekid clip was built for him.
            add(voices.species_aliases.get(species, ""))
            return names
        stem = trimmed


def speaker_species(display_id: int | None, model_file_id: int | None) -> str | None:
    """The species a speaker's model belongs to (species_models.json), or None: by its
    display's model where the display is known, else by the captured model file."""
    species = None
    if display_id:
        cdi = load_db2("CreatureDisplayInfo").get(int(display_id))
        model = load_db2("CreatureModelData").get(int((cdi or {}).get("ModelID") or 0))
        species = _species_by_model_file().get(
            int((model or {}).get("FileDataID") or 0)
        )
    if not species and model_file_id:
        species = _species_by_model_file().get(int(model_file_id))
    return species


def species_voice(
    display_id: int | None,
    sex_id: int | None,
    model_file_id: int | None = None,
    *,
    voices: Voices,
) -> str | None:
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
    species = speaker_species(display_id, model_file_id)
    if not species:
        return None
    for name in species_voice_names(species, sex_id, voices):
        if (VOICES_DIR / f"{name}.wav").exists():
            return name
    return None


@functools.cache
def _race_sex_by_model_file() -> dict[int, Counter]:
    """{CreatureModelData.FileDataID: Counter of (DisplayRaceID, DisplaySexID)} over every
    CreatureDisplayInfo row that uses the model and has an "extra" record.

    Built once: CreatureModelData.FileDataID -> CreatureModelData.ID -> CreatureDisplayInfo.ModelID
    -> CreatureDisplayInfo.ExtendedDisplayInfoID -> CreatureDisplayInfoExtra.
    """
    # Several CreatureModelData rows can share one file, so map by model ID, not by file
    file_by_model = {
        mid: int(row["FileDataID"] or 0)
        for mid, row in load_db2("CreatureModelData").items()
    }
    extras = load_db2("CreatureDisplayInfoExtra")
    result: dict[int, Counter] = {}
    for cdi in load_db2("CreatureDisplayInfo").values():
        fdid = file_by_model.get(int(cdi.get("ModelID") or 0))
        if not fdid:
            continue
        extra = extras.get(int(cdi.get("ExtendedDisplayInfoID") or 0))
        if extra:
            result.setdefault(fdid, Counter())[
                (int(extra["DisplayRaceID"]), int(extra["DisplaySexID"]))
            ] += 1
    # The client reports a player-race NPC's legacy model or its HD twin, by
    # reader, and no display here uses the legacy file: read it as the twin, or
    # it names no race and the speaker falls to human (Hadric Harlson, #810)
    for legacy, hd in _character_model_twins().items():
        if legacy not in result and hd in result:
            result[legacy] = result[hd]
    return result


def _character_model_twins() -> dict[int, int]:
    """{legacy FileDataID: HD FileDataID} for player-race models, from
    tools/data/character_models.json (build_species_map.py)."""
    path = DATA_DIR / "character_models.json"
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def model_race_sex(
    model_file_id: int | None,
    sex_id: int | None = None,
    preferred_races: set[int] | frozenset[int] = frozenset(),
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


def display_sound_set(display_id: int | None) -> int | None:
    """The NPCSounds row a creature display is cast with, or None.

    Blizzard assigns every display one of several voice sets per race and gender -
    a young one, a warrior, an elder - and that assignment is the casting decision
    for that NPC. Pooling them into one reference per race averages the cast away
    and lets a rare archetype speak for everyone (see build_voice_references).
    """
    if not display_id:
        return None
    row = load_db2("CreatureDisplayInfo").get(int(display_id))
    sound_id = int(row.get("NPCSoundID") or 0) if row else 0
    return sound_id if sound_id and sound_id in load_db2("NPCSounds") else None


@functools.cache
def sound_set_displays() -> dict[str, Counter]:
    """{voice name: Counter of NPCSounds set -> how many displays are cast with it}."""
    counts: dict[str, Counter] = {}
    npc_sounds = load_db2("NPCSounds")
    extra = load_db2("CreatureDisplayInfoExtra")
    for row in load_db2("CreatureDisplayInfo").values():
        sound_id = int(row.get("NPCSoundID") or 0)
        extra_id = int(row.get("ExtendedDisplayInfoID") or 0)
        if not sound_id or sound_id not in npc_sounds or extra_id not in extra:
            continue
        race = RACE_DICT.get(int(extra[extra_id]["DisplayRaceID"]))
        gender = GENDER_DICT.get(int(extra[extra_id]["DisplaySexID"]))
        if race and gender:
            counts.setdefault(f"{race}-{gender}", Counter())[sound_id] += 1
    return counts


def dominant_sound_set(voice: str) -> int | None:
    """The archetype most of a voice's NPCs are cast with - what the plain
    <race>-<gender> clip sounds like, and the fallback for a speaker whose own
    display we cannot see."""
    counts = sound_set_displays().get(voice)
    return counts.most_common(1)[0][0] if counts else None


def base_voice(name: str) -> str:
    """`dwarf-male-guard` -> `dwarf-male`; anything else unchanged.

    A voice is `<race>-<gender>`, so a third segment is what makes it an archetype.
    Splitting on the segment rather than matching `-s<digits>` is what lets an archetype
    be called something a person can read.
    """
    parts = name.split("-")
    return "-".join(parts[:2]) if len(parts) > 2 else name


def is_archetype(name: str) -> bool:
    return len(name.split("-")) > 2


@functools.cache
def archetype_names(voice: str) -> dict[int, str]:
    """NPCSounds set -> the clip name for it, unique within this voice.

    Blizzard files each set's recordings under a folder that says what it is, so the
    clip can be `dwarf-male-guard` instead of `dwarf-male-s37` (see tools/soundpaths.py).
    Several sets share a folder - 36 and 156 are both dwarfmalegrimnpc, the same audio
    cast twice - so the set the most displays use takes the bare name and the rest keep
    their row id, which is unique by construction. A set with no folder in the map keeps
    `s<id>` too, so a name never depends on the map being complete.
    """
    from tools.soundpaths import descriptor

    names: dict[int, str] = {}
    taken: set[str] = set()
    for sound_id, _ in (sound_set_displays().get(voice) or Counter()).most_common():
        word = descriptor(sound_id, voice)
        if word and word not in taken:
            taken.add(word)
            names[sound_id] = f"{voice}-{word}"
        else:
            names[sound_id] = f"{voice}-s{sound_id}"
    return names


@functools.cache
def archetype_of(name: str) -> tuple[str, int] | None:
    """`dwarf-male-guard` -> ("dwarf-male", 37), for reading a clip name back."""
    voice = base_voice(name)
    for sound_id, minted in archetype_names(voice).items():
        if minted == name:
            return voice, sound_id
    return None


@functools.cache
def sibling_sets(voice: str, sound_id: int) -> tuple[int, ...]:
    """The voice's other sets recorded by the same actor as `sound_id`, most-used first.

    Filed in the same sound folder and holding at least half of this set's greetings
    and farewells: sets 59 and 121 are both nightelfmalestandardnpc, 121 being 59's
    fourteen files and two more. The folder alone is not enough, since Forever's own
    sets sit in unnamed folders (`7478494`) that hold several skyborne actors and share
    no file between them.
    """
    from tools.build_voice_references import set_fdids
    from tools.soundpaths import folders

    folder = folders().get(sound_id)
    if not folder:
        return ()
    own = set(set_fdids(sound_id))
    found = []
    for other, _ in (sound_set_displays().get(voice) or Counter()).most_common():
        if other == sound_id or folders().get(other) != folder:
            continue
        if own and len(own & set(set_fdids(other))) * 2 >= len(own):
            found.append(other)
    return tuple(found)


def archetype_voice(voice: str, display_id: int | None) -> str | None:
    """`<race>-<gender>-<word>` when this display's archetype has a clip of its own.

    A set with no clip borrows one from a sibling (sibling_sets), the most-used first:
    Blizzard cast the same actor twice or more, and only the most-used of them is
    minted a name and built, so the rest used to fall to the plain race voice while
    barking in the archetype's voice in game. A sibling's own clip, once built, wins
    over the borrowed one.

    None when the set belongs to another race (26 sets span more than one, and
    trusting the name alone once had night elves read by a blood elf recording), and
    when the archetype clip is byte-identical to the plain voice, which happens for a
    race with one set and no spoken emotes - regenerating those under a new name
    would cost GPU for the same audio.
    """
    return set_voice(voice, display_sound_set(display_id))


def set_voice(voice: str, sound_id: int | None) -> str | None:
    """The archetype a set's NPCs are cast on, its own clip or a sibling's, or None
    when they read the plain `voice` (archetype_voice has the rules)."""
    if not sound_id or sound_id not in (sound_set_displays().get(voice) or {}):
        return None
    names = archetype_names(voice)
    plain = VOICES_DIR / f"{voice}.wav"
    for candidate in [sound_id, *sibling_sets(voice, sound_id)]:
        name = names.get(candidate, f"{voice}-s{candidate}")
        clip = VOICES_DIR / f"{name}.wav"
        if not clip.exists():
            continue
        if (
            plain.exists()
            and clip.stat().st_size == plain.stat().st_size
            and clip.read_bytes() == plain.read_bytes()
        ):
            return None
        return name
    return None


def voice_for_npc(
    npc: dict | None,
    zone: str | None = None,
    *,
    voices: Voices | None = None,
    speaker: str | None = None,
) -> str:
    """Picks a `race-gender` voice name for a captured NPC record.

    In order: `[voices.speakers]` for this `speaker` key, the NPC's own cloned clip
    (npc-<displayID>.wav), the race and sex of its displayID, the same via its
    modelFileID (the Forever client provides the model file but never the display
    ID; the Classic export supplies display IDs for unchanged NPCs), a species clip,
    `[voices.sound_sets]` for the display's NPCSounds set, the zone hint, then human. Sex falls back to the in-game UnitSex (2 male, 3 female); game
    objects, items and genderless units go to the narrator.
    `voices` ([voices] in configs/voices.toml) defaults to the repository's.
    """
    if voices is None:
        voices = load_config().voices
    if speaker is not None and str(speaker) in voices.speakers:
        return voices.speakers[str(speaker)]
    if not npc or npc.get("isObject") or npc.get("isObjectOrItem"):
        return voices.narrator
    display_id = npc.get("displayID")
    if display_id and (VOICES_DIR / f"npc-{int(display_id)}.wav").exists():
        return f"npc-{int(display_id)}"  # cloned from this NPC's own recorded greetings
    unit_sex = {2: 0, 3: 1}.get(npc.get("sex"))
    hint = voices.zone_hints.get(zone or npc.get("zone") or "")
    race_id, sex_id = display_race_sex(display_id)
    if race_id is None:
        hinted = (
            {rid for rid, name in RACE_DICT.items() if name == hint} if hint else set()
        )
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
        own = species_voice(display_id, sex_id, npc.get("modelFileID"), voices=voices)
        if own:
            return own
        # The set Blizzard cast the display with still says who it is when the
        # model does not: Gryfe and Bragok bark goblinmalezanynpc on a model the
        # client tables give no race
        by_set = voices.sound_sets.get(str(display_sound_set(display_id) or ""))
        if by_set:
            return by_set
        race = hint or "human"
    if sex_id is None:
        return voices.narrator
    voice = f"{race}-{GENDER_DICT[sex_id]}"
    # Prefer the archetype this display is cast with; the plain race and gender clip
    # stays the fallback for speakers reached through a model file, which names no
    # display and so no archetype
    return archetype_voice(voice, display_id) or voice


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
                result.append(
                    (race, int(entry["SoundKitID"]), int(entry["FileDataID"]))
                )
    return sorted(set(result))


if __name__ == "__main__":
    lines = skyborne_voice_lines()
    print(f"{len(lines)} Skyborne voice files, e.g. {lines[:5]}")
    print("display 3157 ->", display_race_sex(3157))
    print(
        "model file 997378 ->",
        model_race_sex(997378),
        "(scourge; UnitSex male narrows to",
        model_race_sex(997378, 0),
        ")",
    )
    print("model file 7478487 ->", model_race_sex(7478487), "(skyborne male)")
    print(
        "model file 1100258 ->",
        model_race_sex(1100258),
        "(blood elf model; Zephras Isle hint gives",
        model_race_sex(1100258, 1, {95, 96}),
        ")",
    )
