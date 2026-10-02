"""Blizzard's own name for each NPC voice set, from the CASC file paths.

`NPCSounds` rows carry four sound-kit columns and no name, so an archetype could only
be called `<race>-<gender>-s<row id>` - stable, and meaningless to read. The recordings
themselves are filed under names that say exactly what the set is:

    set 36  sound/creature/dwarfmalegrimnpc/dwarfmalegrimnpcgreeting01.ogg      -> grim
    set 37  sound/creature/dwarfmaleguardnpc/dwarfmaleguardnpcgreeting01.ogg    -> guard
    set 50  sound/creature/humanmalewarriornpc/humanmalewarriornpcgreeting01.ogg -> warrior
    set 172 sound/creature/magathagrimtotem/magathagrimtotemgreeting01.ogg      -> a character

Those paths are not in the client tables - CASC stores files by FileDataID - so they
come from the community listfile, fetched once and boiled down to the sets this pack
uses. The result is committed (`tools/data/sound_sets.json`), because a name decides a
voice's file name, and a file name decides which lines regenerate: a map that drifted
under us would restage a voice for nothing.

    ./tools/run.sh fvo-soundpaths            # show what each set is called
    ./tools/run.sh fvo-soundpaths --refresh  # re-derive from the listfile (~100 MB once)
    ./tools/run.sh fvo-soundpaths --folders  # the named sets' folders, file by file

A named character's folder holds far more than its NPCSounds kit: Sylvanas's set has
four greetings, and `sylvanaswindrunner/` about ninety more lines this client ships
(Wrath Gate, Halls of Reflection, aggro), none of them in any kit the beta's tables
link. `--folders` lists every file in the folder of each named set (few enough displays
to be one character, `NAMED_MAX_DISPLAYS`) plus the extra folders of
`[voices.named_folders]`, fetches each once to drop the ones this build does not carry
(`wowdata.is_dud`), and commits the rest with their lengths to
`tools/data/named_folders.json`, which is where `fvo-refclips` and the audition page's
Source clips panel find a named voice's candidates. It is kept apart from `--refresh`
so adding a folder never re-derives the archetype names. It streams the community
listfile, not build_retail_references' pinned verified one, which lacks real lines of
this build (`vo_920_sylvanas_*`, 3 to 12 s each).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from tools.config import DATA_DIR, GENDER_DICT, RACE_DICT, Voices

SOUND_SETS = DATA_DIR / "sound_sets.json"
LISTFILE_URL = (
    "https://github.com/wowdev/wow-listfile/releases/latest/download/"
    "community-listfile.csv"
)
# The folder is named for the kit, the file for the line inside it; both carry the same
# stem, so the folder alone is enough and is stable across the numbered takes.
SOUND_PATH = re.compile(r"^sound/creature/([^/]+)/", re.IGNORECASE)
NAMED_FOLDERS = DATA_DIR / "named_folders.json"
# `.ogg` only: the listfile also names a `.ogg.meta` beside some lines, and wago answers
# those with a 400. Folder names may hold spaces (`witch doctor`, `wolf rider`).
# sound/character/ too: player voice sets (`pcdhnightelfmale`, Legion's night elf demon
# hunter) carry connected flirt and hello lines that no NPC set of the race has.
FOLDER_FILE = re.compile(
    r"^sound/(?:creature|character)/([^/]+)/([^/]+)\.ogg$", re.IGNORECASE
)
PROBE_WORKERS = 4  # wago already answers the odd 504 to one client at a time


def kit_first_file() -> dict[int, int]:
    """NPCSounds row -> the FileDataID of its first greeting, which names the folder."""
    from tools.wowdata import load_db2  # only --refresh needs the client tables

    kits: dict[int, list[int]] = {}
    for row in load_db2("SoundKitEntry").values():
        kits.setdefault(int(row["SoundKitID"]), []).append(int(row["FileDataID"]))
    first: dict[int, int] = {}
    for sound_id, row in load_db2("NPCSounds").items():
        for col in ("SoundID_0", "SoundID_1", "SoundID_2", "SoundID_3"):
            files = kits.get(int(row.get(col) or 0))
            if files:
                first[int(sound_id)] = min(files)
                break
    return first


def refresh() -> dict[str, str]:
    """Downloads the listfile and keeps only the folder of each NPCSounds set."""
    # several NPCSounds rows can name the same sound kit - sets 36 and 156 are both
    # dwarfmalegrimnpc - so one FileDataID answers for a list of sets, not one
    wanted: dict[int, list[int]] = {}
    for sound_id, fdid in kit_first_file().items():
        wanted.setdefault(fdid, []).append(sound_id)
    found: dict[str, str] = {}
    print(
        f"looking up {len(wanted)} sound sets in the community listfile",
        file=sys.stderr,
    )
    with requests.get(LISTFILE_URL, stream=True, timeout=600) as response:
        response.raise_for_status()
        for raw in response.iter_lines():
            if not raw:
                continue
            fdid, _, path = raw.decode("utf-8", "replace").partition(";")
            if not fdid.isdigit():
                continue
            sets = wanted.get(int(fdid))
            if not sets:
                continue
            match = SOUND_PATH.match(path)
            if match:
                for sound_id in sets:
                    found[str(sound_id)] = match.group(1).lower()
    SOUND_SETS.write_text(
        json.dumps(found, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"{len(found)} of {len(wanted)} sets named, written to {SOUND_SETS}",
        file=sys.stderr,
    )
    return found


def _listfile_lines() -> Iterator[str]:
    with requests.get(LISTFILE_URL, stream=True, timeout=600) as response:
        response.raise_for_status()
        for raw in response.iter_lines():
            if raw:
                yield raw.decode("utf-8", "replace")


def folder_entries(
    lines: Iterable[str], wanted: set[str]
) -> dict[str, list[tuple[int, str]]]:
    """listfile lines -> {folder: [(fdid, file stem)]} for the wanted folders, by fdid."""
    found: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for line in lines:
        fdid, _, path = line.rstrip("\r\n").partition(";")
        match = FOLDER_FILE.match(path)
        if fdid.isdigit() and match and match.group(1).lower() in wanted:
            found[match.group(1).lower()].append((int(fdid), match.group(2).lower()))
    return {folder: sorted(files) for folder, files in sorted(found.items())}


def named_set_folders() -> dict[int, str]:
    """NPCSounds row -> sound folder, for the sets that belong to one character."""
    from tools.build_voice_references import named_sets  # needs the client tables

    known = folders()
    return {sound_id: known[sound_id] for sound_id in named_sets() if sound_id in known}


def _probe(fdid: int, where: Path) -> float | None:
    """The file's length in seconds, or None when this build does not carry it.

    Only wago saying so (its JSON answer, a 400 or a 404) or a dud file counts as not
    carried. Anything else - a 429, a 5xx, a timeout, a file ffprobe cannot read -
    raises after two retries: a download that failed is not a line the client lacks,
    and committing it as one would hide a real line.
    """
    from tools.build_voice_references import duration
    from tools.wowdata import fetch_file, is_dud

    for attempt in range(3):
        try:
            path = fetch_file(fdid, where / f"{fdid}.ogg")
            break
        except FileNotFoundError:
            return None
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (400, 404):
                return None
            if attempt == 2:
                raise
        except requests.RequestException:
            if attempt == 2:
                raise
        time.sleep(5 * 3**attempt)
    try:
        return None if is_dud(path) else duration(path) or None
    finally:
        path.unlink()


def _probe_or_error(fdid: int, where: Path) -> float | None | Exception:
    try:
        return _probe(fdid, where)
    except (requests.RequestException, subprocess.CalledProcessError, OSError) as e:
        return e


def refresh_folders(
    voices: Voices, only: set[str] | None = None
) -> dict[str, list[list]]:
    """Writes named_folders.json: every real line in each named set's folder, the
    folders [voices.named_folders] adds, and [voices] clip_folders.

    Files that fail in the pool are tried once more, one at a time, at the end; if any
    still fail, nothing is written and they are listed, so a rerun is the fix. `only`
    probes just those folders and keeps every other one as the file has it: a full run
    is 3,500 files on wago, a new clip folder a few hundred.
    """
    wanted = set(named_set_folders().values())
    for extras in voices.named_folders.values():
        wanted.update(folder.lower() for folder in extras)
    wanted.update(voices.clip_folders)
    if only is not None:
        wanted = {folder.lower() for folder in only}
    print(
        f"looking up {len(wanted)} folders in the community listfile", file=sys.stderr
    )
    entries = folder_entries(_listfile_lines(), wanted)
    files = [
        (folder, fdid, stem) for folder, rows in entries.items() for fdid, stem in rows
    ]
    print(f"probing {len(files)} files on wago", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        with ThreadPoolExecutor(PROBE_WORKERS) as pool:
            lengths = list(pool.map(lambda f: _probe_or_error(f[1], Path(tmp)), files))
        for i, (_, fdid, _) in enumerate(files):
            if isinstance(lengths[i], Exception):
                lengths[i] = _probe_or_error(fdid, Path(tmp))
    failed = [
        (fdid, result)
        for (_, fdid, _), result in zip(files, lengths, strict=True)
        if isinstance(result, Exception)
    ]
    if failed:
        for fdid, error in failed:
            print(f"failed {fdid}: {error}", file=sys.stderr)
        raise SystemExit(
            f"{len(failed)} of {len(files)} files could not be probed; "
            f"{NAMED_FOLDERS.name} left as it was"
        )
    # a wanted folder the listfile does not have stays out, so the audition page keeps
    # warning about a mistyped [voices.named_folders] entry
    kept: dict[str, list[list]] = {folder: [] for folder in entries}
    if only is not None and NAMED_FOLDERS.exists():
        previous = json.loads(NAMED_FOLDERS.read_text(encoding="utf-8"))
        kept = {**{f: r for f, r in previous.items() if f not in wanted}, **kept}
    for (folder, fdid, stem), seconds in zip(files, lengths, strict=True):
        if isinstance(seconds, float):
            kept[folder].append([fdid, stem, round(seconds, 3)])
    NAMED_FOLDERS.write_text(
        json.dumps(kept, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    missing = sorted(wanted - set(entries))
    print(
        f"{sum(len(kept[f]) for f in entries)} of {len(files)} files are real audio, "
        f"written to {NAMED_FOLDERS}"
        + (f"; not in the listfile: {', '.join(missing)}" if missing else ""),
        file=sys.stderr,
    )
    return kept


def named_folder_files() -> dict[str, list[tuple[int, str, float]]]:
    """folder -> [(fdid, file stem, seconds)] from named_folders.json, {} before --folders."""
    if not NAMED_FOLDERS.exists():
        return {}
    data = json.loads(NAMED_FOLDERS.read_text(encoding="utf-8"))
    return {
        folder: [(int(fdid), str(stem), float(seconds)) for fdid, stem, seconds in rows]
        for folder, rows in data.items()
    }


def folders() -> dict[int, str]:
    if not SOUND_SETS.exists():
        return {}
    return {
        int(k): v for k, v in json.loads(SOUND_SETS.read_text(encoding="utf-8")).items()
    }


def descriptor(sound_id: int, voice: str) -> str | None:
    """`guard` for dwarf-male's set 37, or None when the folder says nothing new.

    The folder is the race, the gender and then what kind of NPC it is - so the useful
    part is what is left once the voice's own name is taken off the front and the "npc"
    marker off the back. A folder that is not built that way belongs to one character
    (magathagrimtotem), and a character is not an archetype of its race.
    """
    folder = folders().get(sound_id)
    if not folder:
        return None
    race, _, gender = voice.partition("-")
    for prefix in (f"{race}{gender}", f"{_client_race(race)}{gender}"):
        if prefix and folder.startswith(prefix):
            rest = folder[len(prefix) :]
            rest = re.sub(r"npc$", "", rest).strip("_-")
            return rest or "standard"
    return None


def other_sex_set(sound_id: int, voice: str) -> bool:
    """True when the set's folder is filed under the other sex than `voice`'s.

    A few displays are cast with a set recorded for the other sex: three night elf
    male displays use set 52, nightelffemalesentinelnpc, the voice of 187 female ones.
    Another race's folder is not enough: blood elves were cast with night elf sets
    (149, nightelfmalestandardnpc, is 35 of blood elf male's displays), and those
    recordings are the race's voice here. A folder named for one character
    (fandralstaghelm) has no sex in its name and is never caught.
    """
    folder = folders().get(sound_id)
    _, _, gender = voice.partition("-")
    others = [g for g in GENDER_DICT.values() if g != gender]
    if not folder or not others:
        return False
    races = {r for r in RACE_DICT.values() if r != "narrator"}
    return any(
        folder.startswith(f"{name}{other}")
        for race in races
        for name in (race, _client_race(race))
        for other in others
    )


def _client_race(race: str) -> str:
    """The pack's race name is not always Blizzard's: scourge is filed as undead."""
    return {"scourge": "undead", "nightelf": "nightelf"}.get(race, race)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--refresh", action="store_true", help="re-derive from the listfile"
    )
    parser.add_argument(
        "--folders",
        action="store_true",
        help="list the named sets' folders file by file (probes each on wago)",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="FOLDER",
        help="with --folders: probe just this folder, keeping the others (repeatable)",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.refresh:
        refresh()
    if args.folders:
        from tools.config import load_config

        refresh_folders(load_config().voices, set(args.only) if args.only else None)
        return 0
    from tools.wowdata import load_db2

    known = folders()
    if not known:
        print("no map yet; run with --refresh")
        return 1
    extra = load_db2("CreatureDisplayInfoExtra")
    voices: dict[int, str] = {}
    for row in load_db2("CreatureDisplayInfo").values():
        sound_id = int(row.get("NPCSoundID") or 0)
        extra_id = int(row.get("ExtendedDisplayInfoID") or 0)
        if not sound_id or extra_id not in extra or sound_id in voices:
            continue
        race = RACE_DICT.get(int(extra[extra_id]["DisplayRaceID"]))
        gender = GENDER_DICT.get(int(extra[extra_id]["DisplaySexID"]))
        if race and gender:
            voices[sound_id] = f"{race}-{gender}"
    print(f"{'set':>6}  {'voice':<18} {'folder':<32} descriptor")
    for sound_id in sorted(known):
        voice = voices.get(sound_id, "")
        name = descriptor(sound_id, voice) if voice else None
        print(f"{sound_id:>6}  {voice:<18} {known[sound_id]:<32} {name or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
