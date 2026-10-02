"""Audition: a local page for hearing one line in one voice under several
reference clips and Chatterbox settings side by side, and keeping what wins.

    uv run audition                    # serves http://127.0.0.1:8765
    uv run audition --port 9000 --open # and opens the browser

On an AMD GPU the same page uses the ROCm wheel, in its own environment so the
default CUDA one (what the nightly run generates with) stays put:

    UV_PROJECT_ENVIRONMENT=.venv-rocm ./tools/run.sh --no-group tts --group tts-rocm audition

"Write to pack" is refused on that build. The sound index would treat the file
as current, and the CUDA nightly would ship it.

One model instance, loaded on the first take and kept. Nothing here goes around
the pipeline's bookkeeping:

- "keep" writes [tts.voices.<voice>], a [pronunciations] entry or a
  [voices.speakers] entry (one NPC always in one voice) into
  forever-vo.toml through tomlkit, so the comments survive, and the file is
  validated by the same models before the old one is replaced;
- "write to pack" regenerates a pack file only under the configuration as
  saved, and stamps the fingerprint generate.py would compute, so the nightly
  run agrees the file is current instead of redoing it or missing the change.

Takes land under tools/data/audition/<session>/ (gitignored) and are served
from there. The page is index.html beside this file: one HTML file, no build.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import io
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from tools import generate
from tools.build_voice_references import (
    S3GEN_SECONDS,
    T3_SECONDS,
    build_picked_reference,
)
from tools.config import (
    BETA_BUILD,
    BETA_DIR,
    CASC_DIR,
    CONFIG_TOML,
    DATA_DIR,
    GENDER_DICT,
    PACK_NAME,
    SOUND_INDEX,
    SOUNDS_DIR,
    VOICES_DIR,
    Config,
    ConfigError,
    VoiceTuning,
    load_config,
)
from tools.generate import (
    Item,
    Synth,
    Variant,
    VoiceCatalog,
    load_items,
    load_sources,
    sound_path,
)
from tools.textclean import clean
from tools.wowdata import (
    archetype_names,
    base_voice,
    dominant_sound_set,
    fetch_file,
    is_archetype,
    sound_set_displays,
)

AUDITION_DIR = DATA_DIR / "audition"
ADDONS_DIR = BETA_DIR / "Interface" / "AddOns"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def addons_from_env() -> Path:
    """Where installed ForeverVO_Data* packs are read from.

    Unset, this is the client's AddOns folder from WOW_DIR, as before.
    AUDITION_ADDONS names another directory of those folders, for a checkout
    with no client. fvo-fetch-packs can fill ./addons; nothing reads it until
    the variable or --addons points there."""
    override = os.environ.get("AUDITION_ADDONS")
    return Path(override) if override else ADDONS_DIR


# ----------------------------------------------------------------------------
# forever-vo.toml edits (comments survive: tomlkit round-trips the document)
# ----------------------------------------------------------------------------


@contextlib.contextmanager
def _config_lock(path: Path) -> Iterator[None]:
    """Serialises read-modify-write of the TOML across requests and processes.

    Endpoints here are sync `def`, so FastAPI runs them in a threadpool and two saves
    genuinely overlap: both parsed the same document, and the second to finish wrote a
    file missing the first one's change while reporting success. The lock file is
    separate from the TOML because the write is a rename, which would drop the lock
    with the inode it was taken on.
    """
    lock = path.with_suffix(".toml.lock")
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _validated_write(path: Path, doc: tomlkit.TOMLDocument) -> Config:
    """Writes the document only if the models accept it; returns the fresh Config.

    The trial file carries this process and thread, so two overlapping saves cannot
    validate or promote each other's document.
    """
    text = tomlkit.dumps(doc)
    trial = path.with_suffix(f".toml.{os.getpid()}.{threading.get_ident()}.audition")
    trial.write_text(text, encoding="utf-8")
    try:
        load_config.cache_clear()
        load_config(trial)
    except ConfigError as e:
        raise HTTPException(400, f"refused, the result would not validate: {e}") from e
    else:
        trial.replace(path)
    finally:
        trial.unlink(missing_ok=True)
        load_config.cache_clear()
    return load_config(path)


def write_tuning(
    path: Path,
    voice: str,
    exaggeration: float,
    cfg_weight: float,
    reference: str | None,
    tempo: float = 1.0,
    pitch: float = 0.0,
) -> Config:
    """Sets [tts.voices.<voice>]; a tuning equal to the defaults with no reference
    removes the entry instead, so the file only lists what differs."""
    with _config_lock(path):
        return _write_tuning(
            path, voice, exaggeration, cfg_weight, reference, tempo, pitch
        )


def _write_tuning(
    path: Path,
    voice: str,
    exaggeration: float,
    cfg_weight: float,
    reference: str | None,
    tempo: float = 1.0,
    pitch: float = 0.0,
) -> Config:
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    tts = doc.get("tts")
    if tts is None:
        tts = tomlkit.table()
        doc["tts"] = tts
    defaults = (
        float(tts.get("exaggeration", 0.45)),
        float(tts.get("cfg_weight", 0.5)),
        float(tts.get("tempo", 1.0)),
        float(tts.get("pitch", 0.0)),
    )
    voices = tts.get("voices")
    if voices is None:
        voices = tomlkit.table(is_super_table=True)
        tts["voices"] = voices
    if reference is None:
        # The page's "Clone from" is empty for "the voice's own (as configured)", which
        # is not the same as "this voice clones from nothing". Keeping a take at the
        # default settings used to delete the whole entry and take the reference with
        # it: dwarf-male lost `reference = "npc-3597"` and its 0.75/0.3 that way, which
        # would have restaged 3,114 files in a voice #18 had already corrected. An
        # existing reference is carried over; clearing one is a TOML edit.
        current = load_config(path).tts.voices.get(voice)
        reference = current.reference if current else None
    if (exaggeration, cfg_weight, tempo, pitch) == defaults and not reference:
        if voice in voices:
            del voices[voice]
    else:
        entry = tomlkit.table()
        if reference:
            entry["reference"] = reference
        entry["exaggeration"] = exaggeration
        entry["cfg_weight"] = cfg_weight
        if tempo != defaults[2]:
            entry["tempo"] = tempo
        if pitch != defaults[3]:
            entry["pitch"] = pitch
        voices[voice] = entry
    return _validated_write(path, doc)


def write_pronunciation(path: Path, word: str, spoken: str) -> Config:
    """Adds or replaces one [pronunciations] entry; an empty spoken form removes it."""
    with _config_lock(path):
        return _write_pronunciation(path, word, spoken)


def _write_pronunciation(path: Path, word: str, spoken: str) -> Config:
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    table = doc.get("pronunciations")
    if table is None:
        table = tomlkit.table()
        doc["pronunciations"] = table
    if spoken:
        table[word] = spoken
    elif word in table:
        del table[word]
    return _validated_write(path, doc)


def write_speaker_voice(path: Path, speaker: str, voice: str) -> Config:
    """Sets [voices.speakers].<speaker>; an empty voice removes the entry, and the
    table with its last one, so the speaker goes back to what the capture says."""
    with _config_lock(path):
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        voices = doc.get("voices")
        if voices is None:
            voices = tomlkit.table()
            doc["voices"] = voices
        speakers = voices.get("speakers")
        if speakers is None:
            speakers = tomlkit.table()
            voices["speakers"] = speakers
        if voice:
            speakers[speaker] = voice
        elif speaker in speakers:
            del speakers[speaker]
        if not speakers:
            del voices["speakers"]
        return _validated_write(path, doc)


def write_approval(path: Path, voice: str, recipe: str | None) -> Config:
    """Sets [voices.approved].<voice> to the recipe heard; None takes the approval off.
    The table stays when it empties, so its comment keeps its place in the file."""
    with _config_lock(path):
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        voices = doc.get("voices")
        if voices is None:
            voices = tomlkit.table()
            doc["voices"] = voices
        approved = voices.get("approved")
        if approved is None:
            approved = tomlkit.table()
            voices["approved"] = approved
        if recipe:
            approved[voice] = recipe
        elif voice in approved:
            del approved[voice]
        return _validated_write(path, doc)


def write_voice_sources(
    path: Path, voice: str, clips: list[int], build: str | None = None
) -> Config:
    """Sets [voices.sources.<voice>].clips, head first; an empty list removes the entry,
    so the file lists only the voices that were picked by ear."""
    with _config_lock(path):
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        voices = doc.get("voices")
        if voices is None:
            voices = tomlkit.table()
            doc["voices"] = voices
        sources = voices.get("sources")
        if sources is None:
            # a super table, or the children render inline instead of as their own
            # [voices.sources.<voice>] headers
            sources = tomlkit.table(is_super_table=True)
            voices["sources"] = sources
        if not clips:
            if voice in sources:
                del sources[voice]
            if not sources:
                # leaving an empty [voices.sources] behind would not round-trip
                del voices["sources"]
        else:
            entry = tomlkit.table()
            array = tomlkit.array()
            array.extend(clips)
            entry["clips"] = array.multiline(len(clips) > 6)
            if build:
                entry["build"] = build
            entry.add(tomlkit.nl())  # a blank line before whatever follows
            sources[voice] = entry
        return _validated_write(path, doc)


def sources_warnings(
    config: Config, voice: str, voices_dir: Path = VOICES_DIR
) -> list[str]:
    """What the owner has to know before picking for this voice, worst first.

    Each of these is a way a pick reaches nobody, or reaches far more lines than the
    name suggests. Warnings rather than refusals: the owner knows the pack better than
    this function does.
    """
    warnings: list[str] = []
    tuning = config.tts.voices.get(voice)
    if tuning and tuning.reference and tuning.reference != voice:
        warnings.append(
            f"[tts.voices.{voice}] clones from {tuning.reference}.wav, so {voice}.wav is not "
            f"what this voice reads - pick for {tuning.reference}, or drop that reference first"
        )
    catalog = VoiceCatalog(config, voices_dir)
    borrowers = []
    for other in _known_voices(config, voices_dir):
        if other == voice:
            continue
        resolved = catalog.resolve(other)
        if resolved.clip and resolved.clip.stem == voice:
            borrowers.append(other)
    if borrowers:
        shown = ", ".join(borrowers[:8]) + ("..." if len(borrowers) > 8 else "")
        warnings.append(
            f"{len(borrowers)} other voice(s) read from {voice}.wav: {shown}"
        )
    narrator = config.voices.narrator
    reads_for_narrator = voice == narrator or (
        not (voices_dir / f"{narrator}.wav").exists()
        and catalog.resolve(narrator).clip == voices_dir / f"{voice}.wav"
    )
    if reads_for_narrator:
        warnings.append(
            "this clip is what the narrator reads: every quest from an object "
            "or item, and every <stage direction>"
        )
    return warnings


def unlisted_folders(config: Config) -> list[str]:
    """[voices.named_folders] entries that named_folders.json does not have yet: a new
    folder is offered only once `fvo-soundpaths --folders` has probed it."""
    from tools.soundpaths import named_folder_files

    listed = named_folder_files()
    missing = sorted(
        {
            folder
            for extras in config.voices.named_folders.values()
            for folder in extras
            if folder.lower() not in listed
        }
    )
    if not missing:
        return []
    return [
        (
            f"[voices.named_folders] names {', '.join(missing)}, which "
            "tools/data/named_folders.json does not list yet: run "
            "./tools/run.sh fvo-soundpaths --folders"
        )
    ]


def unlisted_clip_folders(config: Config) -> list[str]:
    """[voices] clip_folders that named_folders.json does not have yet, which "Also offer
    clips from" would otherwise offer with nothing in them."""
    from tools.soundpaths import named_folder_files

    listed = named_folder_files()
    missing = [f for f in config.voices.clip_folders if f not in listed]
    return [
        f"[voices] clip_folders names {folder}, which tools/data/named_folders.json "
        f"does not list yet: run ./tools/run.sh fvo-soundpaths --folders --only {folder}"
        for folder in missing
    ]


@functools.cache
@functools.cache
def named_display_count(display_id: int) -> int:
    """How many creature displays share this named clip's kit - three at most, by the
    rule that mints them (NAMED_MAX_DISPLAYS)."""
    from tools.build_voice_references import NAMED_MAX_DISPLAYS
    from tools.wowdata import display_sound_set, load_db2

    sound_id = display_sound_set(display_id)
    if not sound_id:
        return 1
    shared = sum(
        1
        for row in load_db2("CreatureDisplayInfo").values()
        if int(row.get("NPCSoundID") or 0) == sound_id
    )
    return min(shared, NAMED_MAX_DISPLAYS) or 1


def npc_labels() -> dict[int, str]:
    """creature display -> something to call its clip, because npc-10357 is nobody.

    A named clip is keyed by display ID, which is all the client offers and all the
    pack needs. For reading, the corpus knows what 22 of them are called, and Blizzard
    files the rest under the creature or character the kit belongs to (voljin, peon,
    wolf rider). Cached for the process: state() is polled every 15 s and load_sources
    walks the whole corpus.
    """
    from tools.soundpaths import folders
    from tools.wowdata import display_sound_set

    with contextlib.redirect_stdout(io.StringIO()):
        sources = generate.load_sources()
    by_display: dict[int, str] = {}
    for npc in sources.get("npcs", {}).values():
        display, name = npc.get("displayID"), npc.get("name")
        if display and name:
            by_display.setdefault(int(display), str(name))
    known = folders()
    labels: dict[int, str] = {}
    for path in VOICES_DIR.glob("npc-*.wav"):
        try:
            display = int(path.stem.split("-", 1)[1])
        except ValueError:
            continue
        folder = known.get(display_sound_set(display) or 0)
        label = by_display.get(display) or folder
        if label:
            labels[display] = label
    return labels


SOURCE_PICKS = DATA_DIR / "source_picks.json"
PICK_HISTORY = 20  # per voice; picking is iterative and the last few are what matter


def pick_history(voice: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Every set of clips a voice has been built from, newest first.

    forever-vo.toml records one pick per voice, the one in force, so each build used to
    erase the one before it - and picking is experimental by nature: the way to find out
    whether a clip belongs at the head is to try it and try the other one. This is the
    undo, and the record of what has already been heard.
    """
    if not SOURCE_PICKS.exists():
        return {}
    data = json.loads(SOURCE_PICKS.read_text(encoding="utf-8"))
    return {voice: data.get(voice, [])} if voice else data


def seed_recipe_history(voice: str) -> None:
    """Record the automatic build as the oldest entry, so picking has an undo.

    Only picks were kept, which left no way back to what the recipes made - and that is
    exactly what someone wants when their own clips are not landing. The recipe writes
    its concat list beside the voice's raw audio and picks write a different file, so
    that list survives the first pick and says which clips it used, in order.
    """
    from tools.refclips import RAW_DIR as CLIP_RAW

    if any(row.get("recipe") for row in pick_history(voice).get(voice, [])):
        return  # already recorded; a voice picked before this existed still needs it
    listing = CLIP_RAW / voice / "concat.txt"
    if not listing.exists():
        return
    clips: list[int] = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        stem = Path(line.split("'")[1]).stem if "'" in line else ""
        if stem.isdigit():
            clips.append(int(stem))
    if not clips:
        return
    with _config_lock(SOURCE_PICKS):
        history = pick_history()
        rows = [row for row in history.get(voice, []) if row.get("clips") != clips]
        # oldest, not newest: it is what the voice was before anyone picked for it
        rows.append(
            {
                "clips": clips,
                "build": None,
                "recipe": True,
                "at": "before picking",
                "seconds": round(_concat_seconds(clips, voice), 1),
            }
        )
        history[voice] = rows[:PICK_HISTORY]
        tmp = SOURCE_PICKS.with_suffix(f".json.{os.getpid()}.part")
        tmp.write_text(
            json.dumps(history, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, SOURCE_PICKS)


def _concat_seconds(clips: list[int], voice: str) -> float:
    """How long those clips run, for the history line."""
    from tools.build_voice_references import duration
    from tools.refclips import RAW_DIR as CLIP_RAW

    total = 0.0
    for fdid in clips:
        path = CLIP_RAW / voice / f"{fdid}.ogg"
        if path.exists():
            try:
                total += duration(path)
            except (subprocess.CalledProcessError, OSError):
                pass
    return total


def record_pick(
    voice: str,
    clips: list[int],
    build: str | None,
    seconds: float,
    recipe: bool = False,
) -> None:
    """Puts this pick at the head of the voice's history, if it is not already there."""
    with _config_lock(SOURCE_PICKS):
        history = pick_history()
        rows = [row for row in history.get(voice, []) if row.get("clips") != clips]
        rows.insert(
            0,
            {
                "clips": clips,
                "build": build,
                "seconds": round(seconds, 1),
                "at": time.strftime("%Y-%m-%d %H:%M"),
                "recipe": recipe,
            },
        )
        history[voice] = rows[:PICK_HISTORY]
        tmp = SOURCE_PICKS.with_suffix(f".json.{os.getpid()}.part")
        tmp.write_text(
            json.dumps(history, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, SOURCE_PICKS)


def reference_seconds(path: Path) -> float:
    from tools.build_voice_references import duration

    try:
        return round(duration(path), 1)
    except (subprocess.CalledProcessError, OSError):
        return 0.0


def restage_note(voice: str, existed: bool, kept: bool) -> str:
    """What still has to happen for a re-cut clip to reach players.

    The two cases differ. A clip that did not exist changes which voice NPCs resolve to,
    and generate.wanted() already restages a changed voice name. A clip that did exist
    changes no name, and its bytes are not fingerprinted - only the recorded pick is, so
    the restage follows from writing [voices.sources], not from rebuilding the wav.
    """
    if not existed:
        return (
            f"{voice}.wav is new. NPCs cast with it resolve to that voice now, so the "
            f"next generate run restages their lines on its own."
        )
    if kept:
        return (
            f"{voice}.wav was re-cut and the picks are saved, so its lines are stale and "
            f"the nightly run will redo them. To do it now: "
            f"./tools/run.sh tools/generate.py --voice {voice}"
        )
    return (
        f"{voice}.wav was re-cut but the picks were not saved, and a clip's audio is in "
        f"no fingerprint - nothing restages. Save the picks, or run: "
        f"./tools/run.sh tools/generate.py --force --voice {voice}"
    )


def _known_voices(config: Config, voices_dir: Path = VOICES_DIR) -> list[str]:
    """Voice names worth resolving: the clips on disk plus anything the TOML names."""
    names = {p.stem for p in voices_dir.glob("*.wav")}
    names |= (
        set(config.tts.voices) | set(config.voices.fallbacks) | {config.voices.narrator}
    )
    names |= set(config.voices.narrator_alternates)
    return sorted(names)


# ----------------------------------------------------------------------------
# The corpus, one row per file base name
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LineRow:
    base: str  # file base name, e.g. 415-accept, m-170-accept, 5688-19cbe7de
    subfolder: str  # Quests | Gossip
    title: str  # quest title or gossip speaker
    speaker: str
    voice: str
    raw: str  # the text as captured
    spoken: str  # what the model is asked to say (cleaned, respelled)
    level: int
    source: str
    speaker_key: str = ""  # "288", "-123" for a game object, "" for an item

    @property
    def haystack(self) -> str:
        return f"{self.base} {self.title} {self.speaker} {self.raw}".lower()


def line_rows(items: list[Item]) -> list[LineRow]:
    rows = []
    for item in items:
        speaker = item.entry.get("name") or (item.npc or {}).get("name") or ""
        title = item.entry.get("title") or speaker
        for variant in item.variants():
            rows.append(
                LineRow(
                    variant.base,
                    item.subfolder,
                    title,
                    speaker,
                    item.voice,
                    item.raw_text,
                    variant.text,
                    int(item.entry.get("level") or 0),
                    item.entry.get("source") or "capture",
                    item.speaker_key or "",
                )
            )
    return rows


def _in_voice(row: LineRow, voice: str, moving: frozenset[str]) -> bool:
    """Spoken in `voice`, or by a speaker who moves to it once its clip exists (a
    species voice no one is cast on yet: the spirit healer for spirithealer-female)."""
    return row.voice == voice or (bool(row.speaker_key) and row.speaker_key in moving)


def random_line(
    rows: list[LineRow],
    voice: str,
    rng: random.Random | None = None,
    moving: frozenset[str] = frozenset(),
) -> LineRow | None:
    """A random line spoken in `voice` (the resolved race-gender or npc voice):
    a quest line when the voice has any, else a gossip line, else None."""
    rng = rng or random.Random()
    mine = [row for row in rows if _in_voice(row, voice, moving)]
    quests = [row for row in mine if row.subfolder == "Quests"]
    pool = quests or mine
    return rng.choice(pool) if pool else None


def search(rows: list[LineRow], query: str, limit: int = 40) -> list[LineRow]:
    """Every word of the query must appear in the base name, title, speaker or text.
    Exact base name and title hits sort first, then by level."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    hits = [row for row in rows if all(w in row.haystack for w in words)]
    q = query.lower().strip()
    hits.sort(
        key=lambda r: (
            r.base.lower() != q,
            r.title.lower() != q,
            q not in r.title.lower(),
            r.level,
            r.base,
        )
    )
    return hits[:limit]


def lines_in_voice(
    rows: list[LineRow],
    voice: str,
    limit: int = 60,
    moving: frozenset[str] = frozenset(),
) -> list[LineRow]:
    """That voice's own lines, the longest first.

    Auditioning a voice on the words it will actually say beats free text, and beats one
    random draw: a reference that holds up on a short greeting can still fall apart on a
    paragraph. Longest first because those are the ones that show a delivery - and
    because the pack's own long lines are what the owner notices in play.
    """
    hits = [row for row in rows if _in_voice(row, voice, moving)]
    hits.sort(key=lambda r: (-len(r.spoken), r.base))
    return hits[:limit]


# ----------------------------------------------------------------------------
# The studio: one model, one corpus, the config file
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SoundPack:
    """A folder of pack audio the page can play: the working folder or an installed pack."""

    key: str  # the folder name, which the /api/pack URLs carry
    label: str  # what the page shows: "working folder", or the manifest's name
    priority: int
    sounds: Path


PACK_FIELD = re.compile(r'^\s*(name|priority)\s*=\s*"?([^",]+)"?', re.MULTILINE)


def sound_packs(
    addons: Path | None = ADDONS_DIR, working: Path = SOUNDS_DIR
) -> list[SoundPack]:
    """Where a line's "in the pack now" comes from: the working folder first, since
    it is what "Write to pack" writes and what the owner's machine holds complete,
    then each pack installed in the client, in the order the addon consults them
    (Packs.lua: higher priority first, then name). A contributor's working folder
    holds a few hundred files and the rest of their audio is in the CurseForge packs,
    so without those every line read "no file in the pack yet". The owner links the
    working folder into the client as ForeverVO_Data_Local; that link is skipped,
    since it is the working folder again."""
    installed: list[SoundPack] = []
    for folder in sorted(addons.glob("ForeverVO_Data*")) if addons else []:
        sounds = folder / "Sounds"
        if not sounds.is_dir() or sounds.resolve() == working.resolve():
            continue
        fields: dict[str, str] = {}
        with contextlib.suppress(OSError):
            manifest = (folder / "Data" / "Pack.lua").read_text(encoding="utf-8")
            for key, value in PACK_FIELD.findall(manifest):
                fields.setdefault(key, value.strip())
        priority = 0
        with contextlib.suppress(ValueError):
            priority = int(fields.get("priority", 0))
        installed.append(
            SoundPack(folder.name, fields.get("name", folder.name), priority, sounds)
        )
    installed.sort(key=lambda pack: (-pack.priority, pack.label))
    return [SoundPack(PACK_NAME, "working folder", 0, working), *installed]


class Studio:
    def __init__(self, config_path: Path = CONFIG_TOML, allow_cpu: bool = False):
        self.config_path = config_path
        self.allow_cpu = allow_cpu
        self.model_lock = threading.Lock()
        self.corpus_lock = threading.Lock()
        self._synth: Synth | None = None
        self._rows: list[LineRow] | None = None
        self._by_base: dict[str, tuple[Item, Variant]] = {}
        self.model_status = "not loaded"
        self.corpus_status = "not loaded"
        self._lines_per_voice: Counter[str] | None = None
        self._species_wanted: dict[str, SpeciesWant] = {}
        self.clips_lock = threading.Lock()
        self._clips: dict[str, list[dict[str, Any]]] = {}
        self.clips_status: dict[str, str] = {}
        # session -> set by the page's Stop button; a run checks it between takes
        self.stops: dict[str, threading.Event] = {}
        threading.Thread(target=self.rows, daemon=True).start()

    def config(self) -> Config:
        load_config.cache_clear()
        return load_config(self.config_path)

    def catalog(self, config: Config | None = None) -> VoiceCatalog:
        return VoiceCatalog(config or self.config())

    def synth(self) -> Synth:
        with self.model_lock:
            if self._synth is None:
                self.model_status = "loading"
                try:
                    self._synth = Synth(
                        self.catalog(), allow_cpu=self.allow_cpu, allow_hip=True
                    )
                except BaseException as e:
                    self.model_status = f"failed: {e}"
                    raise
                self.model_status = "ready"
            return self._synth

    def rows(self) -> list[LineRow]:
        with self.corpus_lock:
            if self._rows is None:
                self.corpus_status = "loading"
                catalog = self.catalog()
                items = load_items(
                    load_sources(), include_progress=True, catalog=catalog
                )
                self._by_base = {
                    v.base: (item, v) for item in items for v in item.variants()
                }
                self._rows = line_rows(items)
                self._species_wanted = species_wanted(items, indexed_voices())
                self.corpus_status = f"{len(self._rows)} lines"
            return self._rows

    def rows_if_loaded(self) -> list[LineRow] | None:
        """The corpus rows, or None while they load; never waits on the corpus lock."""
        return self._rows

    def species_want(self, voice: str) -> SpeciesWant | None:
        return self._species_wanted.get(voice)

    def moving_to(self, voice: str) -> frozenset[str]:
        """The speakers who move to this species voice once its clip exists."""
        want = self.species_want(voice)
        return want.speakers if want else frozenset()

    def spoken_if_loaded(self) -> dict[str, int]:
        """lines_per_voice, or {} while the corpus is still loading, so a request that
        only wants the counts for a label never waits minutes on the corpus lock."""
        return self.lines_per_voice() if self._rows is not None else {}

    def lines_per_voice(self) -> dict[str, int]:
        """How many lines each voice actually speaks - the reason to bother with it.

        Cached beside the corpus it is counted from, because state() is polled every
        15 s and this walks every row.
        """
        rows = self.rows()  # takes corpus_lock itself; must not be held here
        with self.corpus_lock:
            if self._lines_per_voice is None:
                self._lines_per_voice = Counter(row.voice for row in rows)
            return dict(self._lines_per_voice)

    def clips(self, voice: str, refresh: bool = False) -> list[dict[str, Any]] | None:
        """This voice's candidate source clips, or None while a background load runs.

        refclips.candidates downloads every candidate it does not already have and runs
        ffprobe over each, so the first call for a voice takes minutes: it goes on a
        thread and the page polls, the way the corpus already does. Afterwards fetch_file
        short-circuits on the file being there and only ffprobe runs.
        """
        with self.clips_lock:
            if refresh:
                self._clips.pop(voice, None)
            if voice in self._clips:
                return self._clips[voice]
            if self.clips_status.get(voice) == "loading":
                return None
            # a failed attempt must not look like one still running, or Load candidates
            # can never try again and the voice reads "loading" until a restart
            self.clips_status[voice] = "loading"
        threading.Thread(target=self._load_clips, args=(voice,), daemon=True).start()
        return None

    def _load_clips(self, voice: str) -> None:
        from tools import refclips

        try:
            found = [
                {
                    "n": i,
                    "fdid": c.fdid,
                    "kind": c.kind,
                    "group": c.group,
                    "seconds": round(c.seconds, 2),
                    "url": f"/api/clips/audio/{voice}/{c.fdid}.ogg",
                }
                # the page's config, not the repository's: --config may name another
                for i, c in enumerate(
                    refclips.candidates(voice, self.config().voices), 1
                )
            ]
        except Exception as e:  # noqa: BLE001 - the status line is the error report
            with self.clips_lock:
                self.clips_status[voice] = (
                    f"failed: {e} (press Load candidates to retry)"
                )
            return
        with self.clips_lock:
            self._clips[voice] = found
            self.clips_status[voice] = f"{len(found)} clips"

    def forget_corpus(self) -> None:
        """After a config change the spoken text or voices may differ; reload lazily."""
        with self.corpus_lock:
            self._rows = None
            self._lines_per_voice = None
            self._by_base = {}
        threading.Thread(target=self.rows, daemon=True).start()

    def line(self, base: str) -> tuple[Item, Variant]:
        self.rows()
        try:
            return self._by_base[base]
        except KeyError:
            raise HTTPException(404, f"no line with base name {base!r}") from None

    def voices(self) -> list[str]:
        return sorted(p.stem for p in VOICES_DIR.glob("*.wav"))

    def pickable(self) -> list[dict[str, Any]]:
        """Every voice clips can be chosen for, whether or not it has a clip yet.

        The voice list is the wavs on disk, which leaves out the archetypes no recipe
        mints - and those are the ones worth picking for: the game casts 182 archetypes
        and 49 have a file, so 133 sets fall back to the plain race voice. human-male-s50
        is 245 creature displays. wowdata.archetype_voice casts on the file being there,
        so building one is what puts those NPCs on it.
        """
        on_disk = set(self.voices())
        labels = npc_labels()
        counts = sound_set_displays()
        # How many creature displays each voice answers for. An archetype's is its own
        # NPCSounds set; a race voice's is every set of that race and gender, since it
        # is what a speaker falls back to; a named clip's is the handful sharing its kit.
        displays: dict[str, int] = {}
        for race_gender, sets in counts.items():
            displays[race_gender] = sum(sets.values())
            for sound_id, n in sets.items():
                displays[archetype_names(race_gender)[sound_id]] = n
        for display_id in labels:
            displays[f"npc-{display_id}"] = named_display_count(display_id)
        spoken = self.lines_per_voice()

        def label_of(voice: str) -> str | None:
            if not voice.startswith("npc-"):
                return None
            try:
                return labels.get(int(voice.split("-", 1)[1]))
            except ValueError:
                return None

        rows: list[dict[str, Any]] = [
            {
                "voice": v,
                "clip": True,
                "displays": displays.get(v),
                "archetype": is_archetype(v),
                "label": label_of(v),
                "lines": spoken.get(v, 0),
            }
            for v in self.voices()
        ]
        for race_gender, sets_of in counts.items():
            names = archetype_names(race_gender)
            for sound_id, counted in sets_of.items():
                name = names[sound_id]
                if name not in on_disk:
                    rows.append(
                        {
                            "voice": name,
                            "clip": False,
                            "displays": counted,
                            "archetype": True,
                            "label": None,
                            "lines": spoken.get(name, 0),
                        }
                    )
        # A species whose speakers have lines but no clip of their kind: the spirit
        # healer, read as human-female. Building <species>-<sex>.wav is what casts
        # them on it (wowdata.species_voice), so only the species that speak are
        # offered, not all 411 in species_models.json.
        listed = {r["voice"] for r in rows}
        for name, want in self._species_wanted.items():
            count, now = want.lines, want.now
            if name not in listed:
                rows.append(
                    {
                        "voice": name,
                        "clip": False,
                        "displays": None,
                        "archetype": False,
                        "label": None,
                        "lines": count,
                        "species": now,
                    }
                )
        # A race in [voices.fallbacks] reads another race's clip until it has its own:
        # nightborne from nightelf-male.wav. Offered so it can get one; building
        # <race>-<gender>.wav is what takes its speakers off the borrowed voice, since
        # VoiceCatalog prefers a voice's own clip to its fallback.
        listed = {r["voice"] for r in rows}
        speaking = speech_voices()
        for race, borrowed in self.config().voices.fallbacks.items():
            if race == "narrator":
                continue
            for gender in GENDER_DICT.values():
                name = f"{race}-{gender}"
                if name not in listed:
                    rows.append(
                        {
                            "voice": name,
                            "clip": False,
                            "displays": displays.get(name),
                            "archetype": False,
                            "label": None,
                            "lines": spoken.get(name, 0),
                            "borrows": f"{borrowed}-{gender}",
                            # has clips to offer of its own: spoken emotes here or in
                            # retail (nightborne, an allied race), or sets it is cast with
                            "offers": name in speaking or name in counts,
                        }
                    )
        return sorted(rows, key=lambda r: r["voice"])

    def state(self) -> dict[str, Any]:
        config = self.config()
        catalog = self.catalog(config)
        resolved = {}
        for voice in self.voices():
            r = catalog.resolve(voice)
            resolved[voice] = {
                "clip": r.clip.name if r.clip else None,
                "source": r.source,
                "exaggeration": r.settings.exaggeration,
                "cfg_weight": r.settings.cfg_weight,
                "tempo": r.settings.tempo,
                "pitch": r.settings.pitch,
                "reference": r.settings.reference,
                "tuned": catalog.tuned(voice),
            }
        return {
            "config_path": str(self.config_path),
            "voices": self.voices(),
            "pickable": self.pickable(),
            "narrator": config.voices.narrator,
            "defaults": {
                "exaggeration": config.tts.exaggeration,
                "cfg_weight": config.tts.cfg_weight,
                "tempo": config.tts.tempo,
                "pitch": config.tts.pitch,
            },
            "overrides": {
                v: t.model_dump(exclude_none=True) for v, t in config.tts.voices.items()
            },
            "resolved": resolved,
            "pronunciations": config.pronunciations.root,
            "speakers": config.voices.speakers,
            # offered beside the voices in "Also offer clips from", as folder-<name>
            "clip_folders": config.voices.clip_folders,
            # current: heard as it is configured now; stale: approved, then something
            # it reads from changed (VoiceCatalog.recipe)
            "approved": {
                voice: "current" if catalog.recipe(voice) == heard else "stale"
                for voice, heard in config.voices.approved.items()
            },
            "sources": {
                v: e.model_dump(exclude_none=True)
                for v, e in config.voices.sources.items()
            },
            "windows": {"t3": T3_SECONDS, "s3gen": S3GEN_SECONDS},
            "model": self.model_status,
            "corpus": self.corpus_status,
            "bulk_service": bulk_service_state(),
        }


def bulk_service_state() -> str:
    try:
        return (
            subprocess.run(
                ["systemctl", "--user", "is-active", "forever-vo-bulk.service"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            ).stdout.strip()
            or "unknown"
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


# ----------------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str
    reference: str | None = (
        None  # a clip stem to clone from instead of the voice's own resolution
    )
    exaggeration: list[float] = Field(min_length=1, max_length=6)
    cfg_weight: list[float] = Field(min_length=1, max_length=6)
    tempo: list[float] = Field(default=[1.0], min_length=1, max_length=4)
    pitch: list[float] = Field(default=[0.0], min_length=1, max_length=4)
    takes: int = Field(default=1, ge=1, le=5)


class KeepTuning(BaseModel):
    voice: str
    exaggeration: float
    cfg_weight: float
    tempo: float = 1.0
    pitch: float = 0.0
    reference: str | None = None


class KeepPronunciation(BaseModel):
    word: str = Field(min_length=1)
    spoken: str = ""


class KeepApproval(BaseModel):
    voice: str
    approved: bool


class KeepSpeakerVoice(BaseModel):
    speaker: str = Field(pattern=r"^-?[1-9][0-9]*$")
    voice: str = ""  # empty: back to the voice the capture resolves to


class WritePack(BaseModel):
    base: str


class BuildSources(BaseModel):
    """Build a voice's reference from clips chosen by ear, head first."""

    voice: str
    clips: list[int] = Field(min_length=1, max_length=40)
    build: str | None = None
    keep: bool = True  # also write [voices.sources.<voice>] into forever-vo.toml


def run_finished(voice: str, outcome: dict[str, Any]) -> None:
    """A desktop notification that a generate run ended, from the server.

    The page tried the browser's Notification API first, and on the owner's KDE
    desktop it never showed one, while `notify-send` from this process did at once.
    So the server, which runs in the desktop session the page is open in, says it;
    where there is no notify-send (a server on another machine) the page alone does.
    """
    notify = shutil.which("notify-send")
    if not notify:
        return
    if outcome["event"] == "error":
        body = f"failed: {outcome.get('message', '')}"
    else:
        n = outcome.get("count", 0)
        takes = f"{n} take{'' if n == 1 else 's'}"
        body = (
            f"stopped · {takes} done"
            if outcome["event"] == "stopped"
            else f"{takes} done"
        )
    try:
        subprocess.Popen(
            [notify, "--app-name=Forever VO audition", f"Audition: {voice}", body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass  # a missed notification is not worth failing the run's last event over


def local_clip(voice: str, fdid: int, build: str | None) -> Path | None:
    """A clip under this voice's raw folder, linked from the download cache when it was
    fetched for another voice; None when it was never downloaded. No network.

    Clips fetched before the shared cache existed (2026-09-29) are only in the raw
    folder of the voice they were fetched for - Thrall's 523 among them - so the other
    voices' folders are the second place to look."""
    from tools.refclips import RAW_DIR as CLIP_RAW

    dest = CLIP_RAW / voice / f"{fdid}.ogg"
    if dest.exists():
        return dest
    if (CASC_DIR / (build or BETA_BUILD) / f"{fdid}.ogg").exists():
        return fetch_file(fdid, dest, build=build or BETA_BUILD)
    found = next(CLIP_RAW.glob(f"*/{fdid}.ogg"), None)
    if found is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(found, dest)
    except FileExistsError:
        pass  # another request linked it first
    except OSError:
        shutil.copyfile(found, dest)
    return dest


@functools.cache
def speech_voices() -> frozenset[str]:
    """Every race-gender with spoken emotes in this client or in retail, which is what
    refclips.speech_candidates offers a voice with no sets of its own."""
    from tools.build_voice_references import emote_speech_fdids
    from tools.config import RETAIL_BUILD

    found: set[str] = set()
    for build in (BETA_BUILD, RETAIL_BUILD):
        # a table that cannot be had offers nothing, rather than failing /api/state
        with contextlib.suppress(Exception):
            found.update(emote_speech_fdids(build))
    return frozenset(found)


def indexed_voices() -> set[str]:
    """Every voice sound_index.json records a file in."""
    try:
        index = json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {entry.get("v") for entry in index.values() if entry.get("v")}


@dataclass(frozen=True)
class SpeciesWant:
    lines: int
    now: str  # the voice most of those lines are read in today
    speakers: frozenset[str]  # their speaker keys, for the NPC list


def species_wanted(
    items: list[Item], voiced: set[str] | None = None
) -> dict[str, SpeciesWant]:
    """Species voices that would take lines, with the voice those lines have now:
    {"spirithealer-female": SpeciesWant(1, "human-female", {"6491"})}. A speaker counts when its model
    names a species and it is not read in a voice of that species, which happens
    when the species has no clip; the name offered is the most specific
    wowdata.species_voice_names would try.

    `voiced` is every voice the sound index has files in. A species clip is not in
    git, so one that exists on the machine that generates the pack (ogre-male, from
    Warcraft III) can be missing here; offering it would invite a clip that shadows
    that one."""
    from tools.wowdata import (
        display_race_sex,
        speaker_species,
        species_voice_names,
    )

    found: dict[str, Counter[str]] = {}
    speakers: dict[str, set[str]] = {}
    for item in items:
        npc = item.npc or {}
        if npc.get("isObject") or npc.get("isObjectOrItem"):
            continue
        # a speaker's own clip or a pinned voice stays ahead of any species voice
        if (
            item.voice.startswith("npc-")
            or str(item.speaker_key) in item.config.voices.speakers
        ):
            continue
        species = speaker_species(npc.get("displayID"), npc.get("modelFileID"))
        if not species:
            continue
        sex = display_race_sex(npc.get("displayID"))[1]
        if sex is None:
            sex = {2: 0, 3: 1}.get(npc.get("sex"))
        names = species_voice_names(species, sex, item.config.voices)
        if not names or item.voice in names or set(names) & (voiced or set()):
            continue
        found.setdefault(names[0], Counter())[item.voice] += 1
        speakers.setdefault(names[0], set()).add(str(item.speaker_key or ""))
    return {
        name: SpeciesWant(
            sum(now.values()), now.most_common(1)[0][0], frozenset(speakers[name])
        )
        for name, now in sorted(found.items())
    }


def group_about(voice: str, group: str, spoken: dict[str, int]) -> str:
    """One line on what a clip group is, for its heading in the Source clips table.

    For a set: its folder, how many creature displays use it in this race, whether
    the plain voice is built from it, and whether it has an archetype clip of its own
    and how many lines that speaks (`spoken`, empty while the corpus loads)."""
    from tools.refclips import FOLDER_PREFIX
    from tools.soundpaths import folders, named_folder_files

    race_gender = base_voice(voice)
    if group.startswith("set ") and group[4:].isdigit():
        sound_id = int(group[4:])
        counts = sound_set_displays().get(race_gender) or Counter()
        folder = folders().get(sound_id)
        n = counts.get(sound_id, 0)
        about = [folder or "folder unknown", f"{n} display{'' if n == 1 else 's'}"]
        shared = [
            other
            for other in counts
            if other != sound_id and folder and folders().get(other) == folder
        ]
        if shared:
            about.append(
                "same folder as " + ", ".join(f"set {other}" for other in shared)
            )
        if sound_id == dominant_sound_set(race_gender):
            about.append(f"the main set, what {race_gender} sounds like")
        name = archetype_names(race_gender).get(sound_id)
        if name == voice:
            about.append("this voice's own set")
        elif name and (VOICES_DIR / f"{name}.wav").exists():
            lines = spoken.get(name)
            about.append(f"clip {name}" + (f", {lines} lines" if lines else ""))
        else:
            about.append(f"no clip of its own: its NPCs read as {race_gender}")
        return " · ".join(about)
    if group == "speech":
        return f"spoken /joke and /flirt emotes, {race_gender}'s only connected speech"
    if group == "retail speech":
        return f"/joke and /flirt from retail: this client has none for {race_gender}"
    if group == "greetings":
        return "the greeting kit this NPC's sound set links"
    if group == "saved picks from other voices":
        return "named by a saved pick, offered by none of the groups here"
    files = named_folder_files().get(group.lower())
    if files is not None:
        where = (
            "sound folder from [voices] clip_folders"
            if voice.startswith(FOLDER_PREFIX)
            else "sound folder"
        )
        return f"{where} · {len(files)} real lines"
    return ""


def wowhead_url(speaker_key: str) -> str | None:
    """wowhead's page for a speaker: npc=<id> for a creature, object=<id> for a game
    object, whose key is the negated ID."""
    if speaker_key.isdigit():
        return f"https://www.wowhead.com/npc={speaker_key}"
    if speaker_key.startswith("-") and speaker_key[1:].isdigit():
        return f"https://www.wowhead.com/object={speaker_key[1:]}"
    return None


def _voice_label(voice: str, labels: dict[int, str]) -> str:
    """npc-4527 (Thrall), or the name alone for a voice with no character label."""
    if voice.startswith("npc-"):
        try:
            label = labels.get(int(voice.removeprefix("npc-")))
        except ValueError:
            label = None
        if label:
            return f"{voice} ({label})"
    return voice


def _safe(name: str) -> str:
    if not SAFE_NAME.match(name):
        raise HTTPException(400, f"bad name {name!r}")
    return name


def _under(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404, relative)
    return path


def create_app(
    studio: Studio, dev: bool = False, addons: Path | None = ADDONS_DIR
) -> FastAPI:
    """`dev` re-reads index.html on every request, so page edits show on a browser
    refresh; Python edits still need a restart (main's --reload does that)."""
    app = FastAPI(title="Forever Voiceover audition")
    page_file = resources.files(__package__) / "index.html"
    page = page_file.read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page_file.read_text(encoding="utf-8") if dev else page

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return studio.state()

    packs = sound_packs(addons)
    packs_by_key = {pack.key: pack for pack in packs}

    def payload(row: LineRow) -> dict[str, Any]:
        found = next(
            (
                pack
                for pack in packs
                if sound_path(row.subfolder, row.base, sounds_dir=pack.sounds).exists()
            ),
            None,
        )
        return {
            **row.__dict__,
            "exists": found is not None,
            "pack": found.label if found else None,
            "pack_url": f"/api/pack/{found.key}/{row.subfolder}/{row.base}.mp3"
            if found
            else None,
        }

    @app.get("/api/lines")
    def lines(q: str = "", voice: str = "") -> dict[str, Any]:
        """Search by words, or list one voice's own lines when `voice` is given."""
        rows = studio.rows()
        if voice:
            _safe(voice)
            moving = studio.moving_to(voice)
            found = lines_in_voice(rows, voice, moving=moving)
            total = sum(1 for row in rows if _in_voice(row, voice, moving))
            return {
                "rows": [payload(row) for row in found],
                "total": total,
                "voice": voice,
            }
        if not q:
            raise HTTPException(
                400, "give q= words to search for, or voice= to list a voice's lines"
            )
        return {
            "rows": [payload(row) for row in search(rows, q)],
            "total": None,
            "voice": None,
        }

    @app.get("/api/random")
    def random_in_voice(voice: str = Query(min_length=1)) -> dict[str, Any]:
        row = random_line(studio.rows(), _safe(voice), moving=studio.moving_to(voice))
        if row is None:
            raise HTTPException(404, f"no lines are spoken in {voice}")
        return payload(row)

    @app.get("/api/pack/{pack}/{subfolder}/{name}")
    def pack_audio(pack: str, subfolder: str, name: str) -> FileResponse:
        if pack not in packs_by_key:
            raise HTTPException(404, pack)
        if subfolder not in ("Quests", "Gossip"):
            raise HTTPException(404, subfolder)
        return FileResponse(
            _under(packs_by_key[pack].sounds, f"{subfolder}/{_safe(name)}"),
            media_type="audio/mpeg",
        )

    @app.get("/api/audio/{session}/{name}")
    def take_audio(session: str, name: str) -> FileResponse:
        return FileResponse(
            _under(AUDITION_DIR, f"{_safe(session)}/{_safe(name)}"),
            media_type="audio/mpeg",
        )

    @app.get("/api/sessions")
    def sessions(limit: int = Query(default=12, ge=1, le=100)) -> list[dict[str, Any]]:
        """Past generate runs, newest first, so a page reload or a server restart
        does not lose the takes: they are still on disk under tools/data/audition/."""
        out = []
        if not AUDITION_DIR.exists():
            return out
        for folder in sorted(
            (p for p in AUDITION_DIR.iterdir() if p.is_dir()), reverse=True
        )[:limit]:
            takes = []
            for path in sorted(folder.glob("*.mp3")):
                recipe = re.match(
                    r"^(?P<voice>.+)-e(?P<e>[0-9.]+)-c(?P<c>[0-9.]+)(?:-t(?P<t>[0-9.]+))?"
                    r"(?:-p(?P<p>-?[0-9.]+))?-take(?P<take>\d+)\.mp3$",
                    path.name,
                )
                takes.append(
                    {
                        "name": path.name,
                        "url": f"/api/audio/{folder.name}/{path.name}",
                        "voice": recipe["voice"] if recipe else None,
                        "exaggeration": float(recipe["e"]) if recipe else None,
                        "cfg_weight": float(recipe["c"]) if recipe else None,
                        "tempo": float(recipe["t"]) if recipe and recipe["t"] else 1.0,
                        "pitch": float(recipe["p"]) if recipe and recipe["p"] else 0.0,
                        "take": int(recipe["take"]) if recipe else None,
                    }
                )
            if takes:
                out.append({"session": folder.name, "takes": takes})
        return out

    @app.post("/api/generate")
    def generate_takes(request: GenerateRequest) -> StreamingResponse:
        _safe(request.voice)
        if request.reference:
            _safe(request.reference)
        config = studio.config()
        spoken = clean(request.text, pronunciations=config.pronunciations)
        if not spoken:
            raise HTTPException(400, "nothing to say once the text is cleaned")
        # Build one tuning before the stream opens. The settings come from three free
        # text boxes and the models have bounds - tempo is 0.5 to 2.0 - so a typo used
        # to raise inside the generator, after 200 had been sent, and the page could
        # only report that the connection broke. A tempo of 10 looked like a network
        # error.
        for exaggeration, cfg_weight, tempo, pitch in itertools.product(
            request.exaggeration, request.cfg_weight, request.tempo, request.pitch
        ):
            try:
                VoiceTuning(
                    reference=request.reference,
                    exaggeration=exaggeration,
                    cfg_weight=cfg_weight,
                    tempo=tempo,
                    pitch=pitch,
                )
            except ValidationError as e:
                detail = "; ".join(
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )
                raise HTTPException(
                    400,
                    f"{detail} (you gave exaggeration {exaggeration}, "
                    f"cfg_weight {cfg_weight}, tempo {tempo}, pitch {pitch})",
                ) from e
        session = time.strftime("%Y%m%d-%H%M%S")
        out_dir = AUDITION_DIR / session
        out_dir.mkdir(parents=True, exist_ok=True)

        def variant_config(
            exaggeration: float,
            cfg_weight: float,
            tempo: float = 1.0,
            pitch: float = 0.0,
        ) -> Config:
            tuning = VoiceTuning(
                reference=request.reference,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
                tempo=tempo,
                pitch=pitch,
            )
            tts = config.tts.model_copy(
                update={"voices": {**config.tts.voices, request.voice: tuning}}
            )
            return config.model_copy(update={"tts": tts})

        stop = studio.stops.setdefault(session, threading.Event())

        def stream() -> Iterator[str]:
            outcome = None
            try:
                for line in takes():
                    event = json.loads(line)
                    if event["event"] in ("done", "stopped", "error"):
                        outcome = event
                    yield line
            finally:  # also when the page goes away mid-run
                studio.stops.pop(session, None)
                if outcome:
                    run_finished(request.voice, outcome)

        def takes() -> Iterator[str]:
            yield (
                json.dumps({"event": "start", "session": session, "spoken": spoken})
                + "\n"
            )
            try:
                synth = studio.synth()
            except (
                SystemExit,
                RuntimeError,
                OSError,
                ImportError,
            ) as e:  # no GPU (SystemExit), CUDA or model load trouble
                yield json.dumps({"event": "error", "message": str(e)}) + "\n"
                return
            n = 0
            # The model runs once per take of each exaggeration and cfg_weight; every
            # tempo and pitch is then encoded from that same audio, so a sweep over
            # them compares one delivery, not several different takes.
            for exaggeration, cfg_weight in itertools.product(
                request.exaggeration, request.cfg_weight
            ):
                catalog = VoiceCatalog(variant_config(exaggeration, cfg_weight))
                for take in range(1, request.takes + 1):
                    # between takes only: Chatterbox's generate() has no way to be
                    # interrupted, so the take in progress always finishes
                    if stop.is_set():
                        yield json.dumps({"event": "stopped", "count": n}) + "\n"
                        return
                    t0 = time.time()
                    with studio.model_lock:
                        synth.catalog = catalog
                        audio = synth.render(spoken, request.voice)
                    for tempo, pitch in itertools.product(request.tempo, request.pitch):
                        resolved = VoiceCatalog(
                            variant_config(exaggeration, cfg_weight, tempo, pitch)
                        ).resolve(request.voice)
                        settings = resolved.settings
                        n += 1
                        name = (
                            f"{request.voice}-e{exaggeration}-c{cfg_weight}"
                            f"-t{tempo}-p{pitch}-take{take}.mp3"
                        )
                        seconds = synth.encode(
                            audio, out_dir / name, settings.tempo, settings.pitch
                        )
                        yield (
                            json.dumps(
                                {
                                    "event": "take",
                                    "n": n,
                                    "take": take,
                                    "name": name,
                                    "url": f"/api/audio/{session}/{name}",
                                    "seconds": round(seconds, 1),
                                    "elapsed": round(time.time() - t0, 1),
                                    "clip": resolved.clip.name
                                    if resolved.clip
                                    else None,
                                    "source": resolved.source,
                                    "exaggeration": settings.exaggeration,
                                    "cfg_weight": settings.cfg_weight,
                                    "tempo": settings.tempo,
                                    "pitch": settings.pitch,
                                    "reference": request.reference,
                                }
                            )
                            + "\n"
                        )
                        t0 = (
                            time.time()
                        )  # later shifts of the take cost the encode only
            yield json.dumps({"event": "done", "count": n}) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/generate/{session}/stop")
    def stop_takes(session: str) -> dict[str, bool]:
        """Ends a run after the take in progress. False when it has already finished."""
        stop = studio.stops.get(session)
        if stop:
            stop.set()
        return {"stopping": stop is not None}

    @app.post("/api/keep-tuning")
    def keep_tuning(request: KeepTuning) -> dict[str, Any]:
        _safe(request.voice)
        if request.reference:
            _safe(request.reference)
        write_tuning(
            studio.config_path,
            request.voice,
            request.exaggeration,
            request.cfg_weight,
            request.reference,
            request.tempo,
            request.pitch,
        )
        studio.forget_corpus()
        return studio.state()

    @app.post("/api/keep-pronunciation")
    def keep_pronunciation(request: KeepPronunciation) -> dict[str, Any]:
        write_pronunciation(
            studio.config_path, request.word.strip(), request.spoken.strip()
        )
        studio.forget_corpus()
        return studio.state()

    @app.post("/api/keep-approval")
    def keep_approval(request: KeepApproval) -> dict[str, Any]:
        """Marks a voice approved by ear as it is configured now, or takes the mark
        off. Only a note in the TOML: nothing is generated or restaged by it."""
        _safe(request.voice)
        recipe = studio.catalog().recipe(request.voice) if request.approved else None
        write_approval(studio.config_path, request.voice, recipe)
        return studio.state()

    @app.post("/api/keep-speaker-voice")
    def keep_speaker_voice(request: KeepSpeakerVoice) -> dict[str, Any]:
        """Pins one speaker to a voice; the voice-change check in generate.py then
        restages exactly that speaker's files."""
        if request.voice:
            _safe(request.voice)
            if request.voice not in studio.voices():
                raise HTTPException(400, f"no voice named {request.voice}")
        write_speaker_voice(studio.config_path, request.speaker, request.voice)
        studio.forget_corpus()
        return studio.state()

    @app.post("/api/write-pack")
    def write_pack(request: WritePack) -> dict[str, Any]:
        """Regenerates one pack file under the saved configuration and records it
        in sound_index.json the way generate.py would."""
        item, variant = studio.line(_safe(request.base))
        catalog = studio.catalog()
        synth = studio.synth()
        if synth.hip:
            raise HTTPException(
                400,
                "This is the ROCm build. Write to pack stays on the CUDA wheel: "
                "a file made here would fingerprint as current and the nightly run would ship it. "
                "Keep the settings; they are numbers in forever-vo.toml, and the CUDA generator "
                "restages the voice from them.",
            )
        path = sound_path(item.subfolder, variant.base)
        t0 = time.time()
        with studio.model_lock:
            synth.catalog = catalog
            seconds = synth.speak(variant.text, item.voice, path)
        index = (
            json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
            if SOUND_INDEX.exists()
            else {}
        )
        fingerprint = catalog.fingerprint(item.voice, variant.text)
        index[variant.base] = {"d": seconds, "v": item.voice, "t": fingerprint}
        generate.save_sound_index(index, {variant.base})
        return {
            "base": variant.base,
            "voice": item.voice,
            "seconds": round(seconds, 1),
            "elapsed": round(time.time() - t0, 1),
            "fingerprint": fingerprint,
            "pack": "working folder",
            "pack_url": f"/api/pack/{PACK_NAME}/{item.subfolder}/{variant.base}.mp3?t={int(time.time())}",
        }

    @app.get("/api/npcs/{voice}")
    def npcs(voice: str) -> dict[str, Any]:
        """The speakers read in this voice, most lines first, for the page's NPC list.
        A species voice nobody is cast on yet lists the speakers who would move to it
        once its clip exists (`moving`). Creature keys link to wowhead's npc pages,
        game objects (negative keys) to its object pages; items have no key."""
        _safe(voice)
        rows = studio.rows_if_loaded()
        if rows is None:
            return {"voice": voice, "status": "loading", "npcs": []}
        moving = studio.moving_to(voice)
        found: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row.speaker_key
            if not key or not (row.voice == voice or key in moving):
                continue
            entry = found.setdefault(
                key,
                {
                    "key": key,
                    "name": row.speaker or key,
                    "lines": 0,
                    "url": wowhead_url(key),
                    "now": row.voice if row.voice != voice else None,
                },
            )
            entry["lines"] += 1
        listed = sorted(found.values(), key=lambda n: (-n["lines"], n["name"]))
        return {"voice": voice, "status": "ready", "npcs": listed}

    @app.get("/api/clips/audio/{voice}/{name}")
    def clip_audio(voice: str, name: str) -> FileResponse:
        from tools.refclips import RAW_DIR as CLIP_RAW

        return FileResponse(
            _under(CLIP_RAW, f"{_safe(voice)}/{_safe(name)}"), media_type="audio/ogg"
        )

    @app.get("/api/clips/reference/{name}")
    def clip_reference(name: str) -> FileResponse:
        """The built reference itself, so the result can be heard without a generate."""
        return FileResponse(_under(VOICES_DIR, _safe(name)), media_type="audio/wav")

    @app.get("/api/clips/{voice}")
    def clips(voice: str, refresh: bool = False, also: str = "") -> dict[str, Any]:
        """Candidate clips for one voice. Returns status "loading" while the first
        fetch runs; the page polls.

        `also` names other voices whose candidates join the table, each group labelled
        with the voice it came from: Thrall's lines for an orc archetype. They come
        first, the last one added at the top, since one is added to be listened to and
        would otherwise sit below a few hundred of the voice's own rows. A saved pick
        found among none of them is listed anyway, from the download cache, so a
        rebuild cannot drop a clip borrowed in an earlier session. `pending` names the
        borrowed voices still loading. Each row's `source` is "own", "also" or "saved",
        for the group headings the page draws."""
        from tools import refclips

        _safe(voice)
        others = [_safe(v) for v in also.split(",") if v and v != voice]
        found = studio.clips(voice, refresh=refresh)
        seed_recipe_history(voice)
        config = studio.config()
        picked = config.voices.sources.get(voice)
        rows = [{**c, "source": "own"} for c in found or []]
        pending = []
        spoken = studio.spoken_if_loaded()
        about = {c["group"]: group_about(voice, c["group"], spoken) for c in rows}
        if found is not None:
            labels = npc_labels()
            seen = {c["fdid"] for c in rows}
            borrowed: list[dict[str, Any]] = []
            for other in reversed(others):
                theirs = studio.clips(other)
                if theirs is None:
                    pending.append(other)
                    continue
                name = _voice_label(other, labels)
                for c in theirs:
                    if c["fdid"] not in seen:
                        seen.add(c["fdid"])
                        group = f"{name}: {c['group']}"
                        if group not in about:
                            about[group] = group_about(other, c["group"], spoken)
                        borrowed.append({**c, "group": group, "source": "also"})
            rows = borrowed + rows
            saved_build = picked.build if picked else None
            for fdid in picked.clips if picked else []:
                if fdid in seen:
                    continue
                path = local_clip(voice, fdid, saved_build)
                if path:
                    seen.add(fdid)
                    rows.append(
                        {
                            "n": len(rows) + 1,
                            "fdid": fdid,
                            "kind": "saved pick",
                            "group": "saved picks from other voices",
                            "source": "saved",
                            "seconds": round(
                                refclips.CLIP_SECONDS.get(
                                    fdid, saved_build or BETA_BUILD, path
                                ),
                                2,
                            ),
                            "url": f"/api/clips/audio/{voice}/{fdid}.ogg",
                        }
                    )
            refclips.CLIP_SECONDS.save()
        return {
            "voice": voice,
            "status": studio.clips_status.get(voice, "not loaded"),
            "clips": rows,
            "pending": pending,
            # in first-seen order, for the page's folder select
            "groups": list(dict.fromkeys(c["group"] for c in rows)),
            # one line on each, for the table's group headings
            "about": {
                group: about.get(group) or group_about(voice, group, spoken)
                for group in dict.fromkeys(c["group"] for c in rows)
            },
            "picked": picked.clips if picked else [],
            "build": picked.build if picked else None,
            "windows": {"t3": T3_SECONDS, "s3gen": S3GEN_SECONDS},
            "warnings": sources_warnings(config, voice)
            + (unlisted_folders(config) if voice.startswith("npc-") else [])
            + unlisted_clip_folders(config),
            "history": pick_history(voice).get(voice, []),
            "reference_url": (
                f"/api/clips/reference/{voice}.wav"
                if (VOICES_DIR / f"{voice}.wav").exists()
                else None
            ),
        }

    @app.post("/api/clips/build")
    def build_sources(request: BuildSources) -> dict[str, Any]:
        from tools.refclips import RAW_DIR as CLIP_RAW

        voice = _safe(request.voice)
        # The picks can only have come from /api/clips, so they are already downloaded:
        # under this voice's folder, or, borrowed from another voice, in the download
        # cache, linked here locally. Either way this endpoint stays off the network.
        paths = []
        for fdid in request.clips:
            path = local_clip(voice, fdid, request.build)
            if path is None:
                raise HTTPException(404, f"clip {fdid} is not downloaded")
            paths.append(_under(CLIP_RAW, f"{voice}/{path.name}"))
        existed = (VOICES_DIR / f"{voice}.wav").exists()
        try:
            out = build_picked_reference(voice, paths)
        except (RuntimeError, ValueError, subprocess.CalledProcessError) as e:
            raise HTTPException(400, str(e)) from e
        if request.keep:
            write_voice_sources(studio.config_path, voice, request.clips, request.build)
        # A new clip changes what wowdata.archetype_voice casts, which the corpus caches
        studio.forget_corpus()
        seconds = reference_seconds(out)
        record_pick(voice, request.clips, request.build, seconds)
        return {
            **studio.state(),
            "built": out.name,
            "seconds": seconds,
            "existed": existed,
            "history": pick_history(voice).get(voice, []),
            "reference_url": f"/api/clips/reference/{voice}.wav?t={int(time.time())}",
            "restage": restage_note(voice, existed, request.keep),
        }

    @app.post("/api/rebuild-tables")
    def rebuild_tables() -> dict[str, Any]:
        code = generate.main(["--tables-only"])
        return {"ok": code == 0}

    return app


def app_from_env() -> FastAPI:
    """uvicorn's --reload re-imports the module in a fresh process, so main() hands
    its options over through the environment and this factory builds the app."""
    import os

    studio = Studio(
        config_path=Path(os.environ.get("AUDITION_CONFIG", str(CONFIG_TOML))),
        allow_cpu=os.environ.get("AUDITION_CPU") == "1",
    )
    addons = os.environ.get("AUDITION_ADDONS")
    return create_app(
        studio,
        dev=os.environ.get("AUDITION_DEV") == "1",
        addons=Path(addons) if addons else ADDONS_DIR,
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_TOML,
        help="the TOML to read and write (default: the repo's)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="allow generating on the CPU when there is no GPU (very slow)",
    )
    parser.add_argument(
        "--addons",
        type=Path,
        default=addons_from_env(),
        help="directory of ForeverVO_Data* packs played beside the working folder "
        "(default: AUDITION_ADDONS, else the client's AddOns from WOW_DIR)",
    )
    parser.add_argument(
        "--open", action="store_true", help="open the page in the browser"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="for working on the page: index.html is re-read on every request, and a change to a "
        ".py file under tools/ restarts the server (which reloads the model, ~30 s, and "
        "drops a generate in flight)",
    )
    args = parser.parse_args(argv)

    import os

    import uvicorn

    os.environ["AUDITION_CONFIG"] = str(args.config)
    os.environ["AUDITION_CPU"] = "1" if args.cpu else "0"
    os.environ["AUDITION_DEV"] = "1" if args.reload else "0"
    os.environ["AUDITION_ADDONS"] = str(args.addons)
    if not args.addons.is_dir():
        print(f"audition: no {args.addons}, so only the working folder's audio plays")
    url = f"http://{args.host}:{args.port}"
    print(
        f"audition: {url}  (config {args.config}{', reloading on edits' if args.reload else ''})"
    )
    if args.open:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(
        "tools.audition:app_from_env",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="warning",
        reload=args.reload,
        reload_dirs=[str(Path(__file__).resolve().parent.parent)]
        if args.reload
        else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
