# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "chatterbox-tts",
#   "setuptools<81",   # perth (chatterbox's watermarker) still imports pkg_resources
#   "requests",
# ]
# ///
"""Generates missing voice lines with a local TTS model and rebuilds the
ForeverVO_Data voice pack tables.

    ./tools/run.sh tools/generate.py --dry-run          # what would be generated
    ./tools/run.sh tools/generate.py --limit 20         # generate a few
    ./tools/run.sh tools/generate.py                    # everything missing
    ./tools/run.sh tools/generate.py --tables-only      # just rebuild the pack tables

Input is tools/data/capture.json (see ingest.py). Audio goes to
ForeverVO_Data/Sounds/{Quests,Gossip}/ and the tables to ForeverVO_Data/Data/.
Tables are rebuilt from scratch every run from capture.json plus the files that
exist on disk, so the pack always matches what is actually present.

Voices: tools/voices/<race>-<gender>.wav are reference clips for cloning
(build_voice_references.py makes them from the client's own audio). Missing
voices fall back to narrator.wav, then to the model's built-in voice.

Quests read by the narrator (objects and items have no speaker to clone) are
also generated in the alternate voices listed in config.NARRATOR_VOICES, into
Sounds/Quests/Narrator/<voice>/, so players can pick the narrator they like in
the addon's options. Those files sort after everything else, so a time-boxed
run still spends its time on lines that have no audio at all:

    ./tools/run.sh tools/generate.py --narrator-voices none   # skip them
    ./tools/run.sh tools/generate.py --narrator-only          # a dedicated pass

A line that mixes the speaker and the narrator -- "Hmm... <Jorgen looks up at
you.> All right, I'll help ya." -- keeps its whole-line file with the stage
direction left out (older addons play that) and also gets one file per part in
reading order, 1241-p1-complete, 1241-p2-complete, ...: the speaker's words in
their voice and each stage direction in the narrator's, the latter again in
every alternate narrator voice. The addon plays the parts back to back and
swaps in the player's narrator. A line that is only a stage direction has
parts and no whole-line file.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

from config import (CAPTURE_JSON, DATA_DIR, FALLBACK_VOICES, NARRATOR_VOICE, NARRATOR_VOICES,
                    PACK_DATA_DIR, SOUND_INDEX, SOUNDS_DIR, VOICES_DIR)
from luatable import lua_string
from textclean import chunk, clean, has_gender_branch, is_speakable, segments, split_gender
from textkey import text_key
from wowdata import voice_for_npc

QUEST_EVENTS = {"accept": "a", "progress": "p", "complete": "c"}


# ----------------------------------------------------------------------------
# Work items
# ----------------------------------------------------------------------------

class Item:
    def __init__(self, kind: str, key: str, entry: dict, npc: dict | None):
        self.kind = kind                      # "quests" | "gossip"
        self.key = key
        self.entry = entry
        self.npc = npc
        self.voice = voice_for_npc(npc, entry.get("zone"))
        self.raw_text = entry.get("text") or ""
        self.event = entry.get("event") or "gossip"
        self.speaker_key = entry.get("npc")   # "288" or "-123" (game object)

    @property
    def subfolder(self) -> str:
        return "Quests" if self.kind == "quests" else "Gossip"

    @property
    def hash(self) -> str:
        return text_key(self.raw_text, self.entry.get("player"), self.entry.get("class"), self.entry.get("race"))

    @property
    def base_name(self) -> str:
        if self.kind == "quests":
            return f"{int(self.entry['questID'])}-{self.event}"
        speaker = self.speaker_key or "unknown"
        speaker = speaker.replace("-", "obj")
        return f"{speaker}-{self.hash}"

    @property
    def is_narrator(self) -> bool:
        """Lines read by the narrator (objects, items, speakers with no gender)
        get one file per alternate narrator voice."""
        return self.voice == NARRATOR_VOICE

    @property
    def gendered(self) -> bool:
        return has_gender_branch(clean(self.raw_text, keep_stage_directions=self.is_narrator))

    def variants(self) -> list[Variant]:
        """One Variant per file base name; two when the text branches on player gender."""
        raw = self.raw_text
        branches = [("", raw)]
        if self.gendered:
            male, female = split_gender(raw)
            branches = [("m-", male), ("f-", female)]
        out = []
        for prefix, text in branches:
            # The narrator reads a stage direction as prose. A speaker leaves it
            # out of the whole-line file and the line also gets parts, so the
            # narrator can say it between the speaker's words.
            parts = [] if self.is_narrator else segments(text)
            if len(parts) == 1 and parts[0][0] == "npc":
                parts = []
            out.append(Variant(f"{prefix}{self.base_name}", clean(text, keep_stage_directions=self.is_narrator), parts))
        return out


class Variant(NamedTuple):
    """One file base name of a line and what is spoken under it."""
    base: str
    text: str                         # the whole line as its speaker reads it
    parts: list[tuple[str, str]]      # ("npc"|"narrator", words) in reading order when the
                                      # line mixes the speaker and the narrator, else empty


def part_name(base: str, index: int) -> str:
    """File base name of a line's part: 1241-p2-complete, 2492-p1-aa6f2374,
    m-170-p1-accept. The part number sits before the last segment so that the
    name still ends in the quest event or text hash: sound_folder() and every
    older tool that tells quests from gossip by the last segment keep working
    (an older generator still running probes any file it sees under Sounds/)."""
    head, _, last = base.rpartition("-")
    return f"{head}-p{index}-{last}"


# ----------------------------------------------------------------------------
# Narrator alternates
# ----------------------------------------------------------------------------
# The default narrator voice keeps the plain path and the plain sound_index key,
# so nothing about the existing files changes; alternates are namespaced by voice.

def narrator_dir(sounds_dir: Path, voice: str, subfolder: str = "Quests") -> Path:
    return sounds_dir / subfolder / "Narrator" / voice


def sound_path(subfolder: str, base: str, voice: str = NARRATOR_VOICE, sounds_dir: Path = SOUNDS_DIR) -> Path:
    if voice == NARRATOR_VOICE:
        return sounds_dir / subfolder / f"{base}.mp3"
    return narrator_dir(sounds_dir, voice, subfolder) / f"{base}.mp3"


def index_key(base: str, voice: str = NARRATOR_VOICE) -> str:
    """Key under which a file's duration and voice are recorded in sound_index.json.
    The folder is left out: base names already say which one they belong to."""
    return base if voice == NARRATOR_VOICE else f"Narrator/{voice}/{base}"


class Target(NamedTuple):
    """One file to synthesise: a line's gender variant in one voice."""
    item: Item
    base: str
    text: str
    voice: str
    alternate: bool = False   # an alternate narrator voice rather than the line's own

    @property
    def location(self) -> str:
        return self.voice if self.alternate else NARRATOR_VOICE

    @property
    def key(self) -> str:
        return index_key(self.base, self.location)

    @property
    def fingerprint(self) -> str:
        """Hash of the words actually spoken. Recorded alongside the voice so a
        corrected text regenerates, the way a changed voice already does: a quest
        file keeps its <questID>-<event> name, so nothing else would notice."""
        return text_key(self.text)

    @property
    def path(self) -> Path:
        return sound_path(self.item.subfolder, self.base, self.location)

    @property
    def label(self) -> str:
        return f"{self.item.subfolder}/{self.key}.mp3"


def parse_narrator_voices(spec: str | None) -> list[str]:
    """--narrator-voices: 'all', 'none', or a comma separated list. Returns alternates only."""
    alternates = NARRATOR_VOICES[1:]
    if spec in (None, "all"):
        return alternates
    if spec == "none":
        return []
    chosen = [v.strip() for v in spec.split(",") if v.strip()]
    unknown = [v for v in chosen if v not in alternates]
    if unknown:
        raise SystemExit(f"unknown narrator voice(s) {unknown}; known: {', '.join(alternates)}")
    return chosen


SOURCE_ORDER = ["classic", "questcache", "capture"]  # later sources override earlier ones


def load_sources() -> dict:
    """Merges tools/data/bulk/*.json and capture.json field by field, capture winning."""
    merged = {"quests": {}, "gossip": {}, "npcs": {}}
    files = {p.stem: p for p in (DATA_DIR / "bulk").glob("*.json")}
    if CAPTURE_JSON.exists():
        files["capture"] = CAPTURE_JSON
    for name in SOURCE_ORDER + sorted(set(files) - set(SOURCE_ORDER)):
        path = files.get(name)
        if not path:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for kind in ("quests", "gossip", "npcs"):
            for key, entry in data.get(kind, {}).items():
                target = merged[kind].setdefault(str(key), {})
                for field, value in entry.items():
                    if value is None or value == "" or (field in ("displayID", "modelFileID") and not value):
                        continue
                    target[field] = value
        print(f"source {name}: {len(data.get('quests', {}))} quest, {len(data.get('gossip', {}))} gossip, {len(data.get('npcs', {}))} npc entries")
    return merged


def load_items(capture: dict, include_progress: bool) -> list[Item]:
    items = []
    for kind in ("quests", "gossip"):
        for key, entry in capture.get(kind, {}).items():
            if kind == "quests" and entry.get("event") == "progress" and not include_progress:
                continue
            npc = capture.get("npcs", {}).get(str(entry.get("npc") or ""))
            if npc is None and entry.get("isObject"):
                npc = {"isObject": True, "name": entry.get("name")}
            items.append(Item(kind, key, entry, npc))
    return items


# ----------------------------------------------------------------------------
# TTS
# ----------------------------------------------------------------------------

class Synth:
    def __init__(self, device: str = "cuda", allow_cpu: bool = False):
        import perth
        import torch
        if getattr(perth, "PerthImplicitWatermarker", None) is None:
            perth.PerthImplicitWatermarker = perth.DummyWatermarker
        from chatterbox.tts import ChatterboxTTS
        self.torch = torch
        if device == "cuda" and not torch.cuda.is_available():
            # The CPU runs about 0.08x real time against the GPU's 1.5x, so a silent
            # fallback looks like a working run while making ~1 line a minute. A GPU
            # that vanished mid-job usually means a lost context (Xid after suspend:
            # `journalctl -k | grep Xid`), which can take a reboot to clear.
            if not allow_cpu:
                raise SystemExit("CUDA is not available, refusing to generate on the CPU (~18x slower "
                                 "than real time). Check nvidia-smi and the kernel log, or pass --cpu.")
            device = "cpu"
            print("CUDA is not available; generating on the CPU because --cpu was given")
        self.model = ChatterboxTTS.from_pretrained(device=device)
        self.sr = self.model.sr
        self._voice_cache: dict[str, Path | None] = {}

    def reference_for(self, voice: str) -> Path | None:
        if voice not in self._voice_cache:
            race, _, gender = voice.partition("-")
            fallback = FALLBACK_VOICES.get(race)
            candidates = [VOICES_DIR / f"{voice}.wav"]
            if fallback:
                candidates.append(VOICES_DIR / (f"{fallback}-{gender}.wav" if gender else f"{fallback}.wav"))
                candidates.append(VOICES_DIR / f"{fallback}-male.wav")
            candidates.append(VOICES_DIR / "narrator.wav")
            candidates.append(VOICES_DIR / "human-male.wav")
            self._voice_cache[voice] = next((p for p in candidates if p.exists()), None)
        return self._voice_cache[voice]

    def speak(self, text: str, voice: str, out_mp3: Path) -> float:
        reference = self.reference_for(voice)
        pieces = []
        silence = self.torch.zeros(1, int(self.sr * 0.35))
        for part in chunk(text):
            kwargs = {"audio_prompt_path": str(reference)} if reference else {}
            # Chatterbox sometimes answers a short standalone sentence with a
            # blip: "Galgar wipes his brow." came back as 0.36 s where the other
            # narrator voices took 2 s, and 17 stage-direction parts of two to
            # four words were like it (2026-09-23). The output is sampled, so a
            # second try usually speaks; keep the longest of a few.
            floor = max(0.5, 0.15 * len(part.split()))
            best = None
            for attempt in range(3):
                wav = self.model.generate(part, exaggeration=0.45, cfg_weight=0.5, **kwargs).cpu()
                if best is None or wav.shape[-1] > best.shape[-1]:
                    best = wav
                if best.shape[-1] / self.sr >= floor:
                    break
                print(f"    short output ({wav.shape[-1] / self.sr:.2f}s for {len(part.split())} words), retrying")
            pieces.append(best)
            pieces.append(silence)
        audio = self.torch.cat(pieces[:-1], dim=-1)
        duration = audio.shape[-1] / self.sr

        import torchaudio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        # Encoded beside the target and renamed into place: a worker killed mid-encode
        # (unit restart, lost GPU) would otherwise leave a truncated mp3 under the
        # final name, which every later run skips as done. The partial file has no
        # .mp3 suffix so the table rebuild's glob cannot pick it up either.
        out_part = out_mp3.with_suffix(f".{os.getpid()}.part")
        try:
            torchaudio.save(str(tmp_path), audio, self.sr)
            out_mp3.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(tmp_path), "-ac", "1", "-ar", "44100",
                 "-codec:a", "libmp3lame", "-q:a", "4", "-f", "mp3", str(out_part)],
                check=True,
            )
            os.replace(out_part, out_mp3)
        finally:
            tmp_path.unlink(missing_ok=True)
            out_part.unlink(missing_ok=True)
        return duration


# ----------------------------------------------------------------------------
# Pack tables
# ----------------------------------------------------------------------------

def lua_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, dict):   # keyed sub-table, e.g. a duration per narrator voice
        return "{ " + ", ".join(f"[{lua_value(k)}]={lua_value(v)}" for k, v in sorted(value.items())) + " }"
    if isinstance(value, list):   # array, e.g. the parts of a line; dict elements are records
        return "{ " + ", ".join(lua_record(v) if isinstance(v, dict) else lua_value(v) for v in value) + " }"
    raise TypeError(type(value))


def lua_record(fields: dict) -> str:
    parts = [f"{k}={lua_value(v)}" for k, v in fields.items() if v is not None and v is not False]
    return "{ " + ", ".join(parts) + " }"


def write_table(filename: str, field: str, lines: list[str], data_dir: Path = PACK_DATA_DIR,
                pack_global: str = "ForeverVO_DataPack", prelude: str = "") -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines)
    # Written through a temporary file: parallel shards (--shard) both rebuild the
    # tables every 25 files, and the client loads these, so a half-written
    # Quests.lua would be a syntax error in someone's game. Either shard's
    # snapshot is valid on its own, so last writer wins is fine; a torn file is not.
    # The temporary name carries the pid: with one shared name, two coinciding
    # writes would rename the other's half-written file into place.
    target = data_dir / filename
    tmp = target.with_suffix(f"{target.suffix}.{os.getpid()}.tmp")
    tmp.write_text(
        f"-- Generated by tools/generate.py; do not edit.\nlocal pack = {pack_global}\n{prelude}"
        f"pack.{field} = {{\n{body}\n}}\n",
        encoding="utf-8",
    )
    os.replace(tmp, target)


def sound_folder(base: str) -> str:
    """Quests are <questID>-<event>, gossip is <speaker>-<hash>."""
    return "Quests" if base.rsplit("-", 1)[-1] in QUEST_EVENTS else "Gossip"


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def index_rank(value) -> int:
    """How much an index entry knows: 3 for a generator's record (voice and text
    fingerprint), 1 for a pre-fingerprint record, 0 for a duration a table
    rebuild probed from the file, or a legacy bare float. An entry may only
    replace one of equal or lower rank, so a probe never erases a record."""
    if not isinstance(value, dict):
        return 0
    return (1 if value.get("v") is not None else 0) + (2 if "t" in value else 0)


def save_sound_index(sound_index: dict, keys: set[str] | None = None) -> None:
    """Merges this process's entries into the index on disk and replaces the file
    atomically, then brings the in-memory copy up to date with the disk.

    Two workers (--shard) each hold a copy in memory and write every 25 files.
    Only `keys` -- what this process wrote since its last save; everything when
    None -- are merged in, so a stale copy of an entry the other worker has since
    rewritten does not overwrite it, and an entry never replaces one that knows
    more (index_rank), so a duration a table rebuild probed from the other
    worker's fresh file cannot erase that worker's record of its voice and text
    (which is what left 2,790 files with no voice on 2026-09-22). The
    read-merge-write runs under a lock and the temporary name carries the pid,
    so two saves cannot interleave or rename each other's half-written file."""
    SOUND_INDEX.parent.mkdir(parents=True, exist_ok=True)
    lock_path = SOUND_INDEX.with_suffix(".lock")
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        merged: dict = {}
        if SOUND_INDEX.exists():
            try:
                merged = json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                merged = {}
        for key in list(keys if keys is not None else sound_index):
            value = sound_index.get(key)
            if value is None:
                # A key this process dropped: the file behind it was removed (an
                # alternate narrator recording of a line the narrator no longer
                # reads), so the entry goes with it.
                merged.pop(key, None)
            elif index_rank(value) >= index_rank(merged.get(key)):
                merged[key] = value
        tmp = SOUND_INDEX.with_suffix(f".json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(merged, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, SOUND_INDEX)
    for key, value in merged.items():
        if index_rank(value) >= index_rank(sound_index.get(key)):
            sound_index[key] = value
    if keys is not None:
        keys.clear()


def speaker_int(key: str | None) -> int | None:
    try:
        return int(key) if key is not None else None
    except ValueError:
        return None


def rebuild_tables(items: list[Item], sound_index: dict[str, float], data_dir: Path = PACK_DATA_DIR,
                   pack_global: str = "ForeverVO_DataPack", sounds_dir: Path = SOUNDS_DIR, write_index: bool = True,
                   dirty: set[str] | None = None) -> dict:
    """Writes the pack tables for `items` whose audio exists under sounds_dir.
    Returns {"quests": n, "gossip": n, "npcs": n, "files": set(base names),
    "narratorFiles": set(paths relative to Sounds/)}.

    `dirty` is the set of index keys this process has written since its last
    save; the durations probed here join it, and only those keys are merged into
    the index on disk (see save_sound_index)."""
    if dirty is None:
        dirty = set()
    present = {p.stem for p in sounds_dir.glob("*/*.mp3")}
    for name in present:
        if name not in sound_index:
            # A file we have no record of, usually the other worker's, made since
            # we last merged the disk. Its duration is enough for the tables; the
            # rank rule in save_sound_index keeps this from replacing that
            # worker's own record once it saves.
            sound_index[name] = {"d": probe_duration(sounds_dir / sound_folder(name) / f"{name}.mp3"), "v": None}
            dirty.add(name)

    # Alternate narrator voices, one folder each under Quests and Gossip. A line
    # counts as available in a voice when its file is there, whoever generated it,
    # so a half-finished pass still yields a usable (if smaller) menu.
    narrator_present: dict[str, set[str]] = {}
    for voice in NARRATOR_VOICES[1:]:
        names = {p.stem for subfolder in ("Quests", "Gossip")
                 for p in narrator_dir(sounds_dir, voice, subfolder).glob("*.mp3")}
        if not names:
            continue
        narrator_present[voice] = names
        for name in names:
            key = index_key(name, voice)
            if key not in sound_index:
                sound_index[key] = {"d": probe_duration(sound_path(sound_folder(name), name, voice, sounds_dir)), "v": voice}
                dirty.add(key)

    def duration_of(name: str) -> float:
        recorded = sound_index.get(name, 0.0)
        return recorded["d"] if isinstance(recorded, dict) else float(recorded)

    quests: dict[int, dict] = {}
    gossip: dict[int, list[dict]] = {}
    npcs: dict[int, str] = {}
    narrator: dict[int, dict[str, dict]] = {}
    used: set[str] = set()
    narrator_used: set[str] = set()

    for item in items:
        variants = item.variants()
        available = [v.base for v in variants if v.base in present]
        # A line that mixes the speaker and the narrator also has parts, recorded
        # only when every variant has every part; a line that is only a stage
        # direction has parts and no whole-line file.
        part_count = len(variants[0].parts)
        parts_complete = part_count > 0 and all(
            len(v.parts) == part_count and all(part_name(v.base, i) in present for i in range(1, part_count + 1))
            for v in variants)
        if not available and not parts_complete:
            continue
        used.update(available)
        gendered = len(variants) == 2
        duration = round(max(duration_of(base) for base in available), 3) if available else None
        speaker = speaker_int(item.speaker_key)
        name = item.entry.get("name") or (item.npc or {}).get("name")
        if speaker is not None and name:
            npcs[speaker] = name

        # The same line in the alternate narrator voices, each with its own
        # duration: voices differ in pace, and the text is paged against it.
        # Only a line the narrator still reads: a speaker that gains a voice of
        # its own (a dryad once a dryad clip exists) leaves its old alternates
        # on disk until the generator removes them, and the addon would play
        # one of those over the new voice for anyone with an alternate picked.
        alternates: dict[str, float] = {}
        for voice, names in narrator_present.items() if item.is_narrator else ():
            spoken = [v.base for v in variants if v.base in names]
            if not spoken:
                continue
            narrator_used.update(f"{item.subfolder}/Narrator/{voice}/{base}" for base in spoken)
            alternates[voice] = round(max(duration_of(index_key(base, voice)) for base in spoken), 3)

        # Parts: {d, n} per part in reading order (n marks the narrator's), and
        # for the narrator's parts the alternate voices' own durations by index.
        parts_record: list[dict] | None = None
        part_alternates: dict[str, dict[int, float]] = {}
        if parts_complete:
            parts_record = []
            for index in range(1, part_count + 1):
                names = [part_name(v.base, index) for v in variants]
                used.update(names)
                role = variants[0].parts[index - 1][0]
                parts_record.append({"d": round(max(duration_of(n) for n in names), 3),
                                     "n": True if role == "narrator" else None})
                if role != "narrator":
                    continue
                for voice, voice_names in narrator_present.items():
                    spoken = [n for n in names if n in voice_names]
                    if not spoken:
                        continue
                    narrator_used.update(f"{item.subfolder}/Narrator/{voice}/{n}" for n in spoken)
                    part_alternates.setdefault(voice, {})[index] = round(
                        max(duration_of(index_key(n, voice)) for n in spoken), 3)

        if item.kind == "quests":
            quest_id = int(item.entry["questID"])
            letter = QUEST_EVENTS[item.event]
            record = quests.setdefault(quest_id, {})
            if duration is not None:
                record[letter] = duration
            if parts_record:
                record[letter + "P"] = parts_record
            for voice, durations in part_alternates.items():
                narrator.setdefault(quest_id, {}).setdefault(voice, {})[letter + "P"] = durations
            if gendered:
                # $G branches per line, not per quest: quest 170's accept text
                # branches and its complete text does not. One flag for the quest
                # made FindQuest prefix m-/f- onto every event and look up a file
                # that was never written, so the turn-in played nothing. Record the
                # events that actually branched; Packs.lua still accepts `true`.
                letters = set(record.get("g") or "") | {QUEST_EVENTS[item.event]}
                record["g"] = "".join(sorted(letters))
            # wa/wp/wc: the readers ingest.py still wants this event captured by
            # (needs_of: "f" after a male reading of a line the client resolved
            # a $G branch out of, "mf" when the reader is unknown). The addon
            # exports the line again for such a reader although it is voiced.
            if item.entry.get("needs"):
                record["w" + letter] = item.entry["needs"]
            # npc is the giver, which the addon shows for an accept text the
            # client leaves unattributed (an item-started or shared quest);
            # ender the turn-in speaker, for a progress or complete text at a
            # game object. One field for both meant 172 item-started quests
            # wore their turn-in NPC's face while the narrator read.
            if speaker is not None:
                field = "npc" if item.event == "accept" else "ender"
                if record.get(field) is None:
                    record[field] = speaker
            for voice, seconds in alternates.items():
                narrator.setdefault(quest_id, {}).setdefault(voice, {})[QUEST_EVENTS[item.event]] = seconds
        else:
            if speaker is None:
                continue
            gossip.setdefault(speaker, []).append({
                "f": item.base_name,
                "h": item.hash,
                "t": item.raw_text.replace("\r", " ").replace("\n", " "),
                "d": duration,
                "g": gendered or None,
                "n": alternates or None,   # voice -> duration, turned into indices below
                "P": parts_record,
                "nP": part_alternates or None,   # voice -> {part index -> duration}, likewise
            })

    # Only the voices this pack actually carries go in the menu, and the records
    # index into that list, so a voice added to config.py later cannot shift them.
    spoken_voices = {voice for alternates in narrator.values() for voice in alternates}
    spoken_voices.update(voice for entries in gossip.values() for entry in entries
                         for voice in list(entry["n"] or {}) + list(entry["nP"] or {}))
    voices = [voice for voice in NARRATOR_VOICES[1:] if voice in spoken_voices]

    for rec in quests.values():
        if rec.get("ender") == rec.get("npc"):
            rec.pop("ender", None)
    quest_lines = [f"\t[{qid}] = {lua_record(rec)}," for qid, rec in sorted(quests.items())]
    gossip_lines = []
    for speaker, entries in sorted(gossip.items()):
        gossip_lines.append(f"\t[{speaker}] = {{")
        for entry in sorted(entries, key=lambda e: e["f"]):
            if entry["n"]:
                entry["n"] = {voices.index(voice) + 1: seconds for voice, seconds in entry["n"].items()}
            if entry["nP"]:
                entry["nP"] = {voices.index(voice) + 1: durations for voice, durations in entry["nP"].items()}
            gossip_lines.append(f"\t\t{lua_record(entry)},")
        gossip_lines.append("\t},")
    npc_lines = [f"\t[{key}] = {lua_string(name)}," for key, name in sorted(npcs.items())]

    narrator_lines = []
    for quest_id, alternates in sorted(narrator.items()):
        parts = [f"[{voices.index(voice) + 1}]={lua_record(record)}"
                 for voice, record in sorted(alternates.items(), key=lambda pair: voices.index(pair[0]))]
        narrator_lines.append(f"\t[{quest_id}] = {{ " + ", ".join(parts) + " },")
    voice_list = ", ".join(lua_string(voice) for voice in voices)

    write_table("Quests.lua", "quests", quest_lines, data_dir, pack_global)
    write_table("Gossip.lua", "gossip", gossip_lines, data_dir, pack_global)
    write_table("NPCs.lua", "npcs", npc_lines, data_dir, pack_global)
    write_table("Narrator.lua", "narrator", narrator_lines, data_dir, pack_global,
                prelude=f"pack.narratorVoices = {{ {voice_list} }}\n")
    if write_index:
        save_sound_index(sound_index, dirty)
    gossip_count = sum(len(v) for v in gossip.values())
    narrated_gossip = sum(1 for entries in gossip.values() for entry in entries if entry["n"])
    narrator_note = (f", {len(narrator)} quests and {narrated_gossip} gossip lines in {len(voices)} alternate "
                     f"narrator {'voice' if len(voices) == 1 else 'voices'} ({len(narrator_used)} files)") if voices else ""
    print(f"pack tables ({data_dir.parent.name}): {len(quests)} quests, {gossip_count} gossip lines, "
          f"{len(npcs)} speakers, {len(used)} sound files{narrator_note}")
    return {"quests": len(quests), "gossip": gossip_count, "npcs": len(npcs), "files": used,
            "narratorFiles": narrator_used}


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list what would be generated")
    parser.add_argument("--limit", type=int, default=0, help="generate at most N files")
    parser.add_argument("--force", action="store_true", help="regenerate even if a file exists")
    parser.add_argument("--all", action="store_true", help="include texts another pack already covers")
    parser.add_argument("--progress", action="store_true", help="include quest progress texts")
    parser.add_argument("--only", choices=["quests", "gossip"], help="restrict to one kind")
    parser.add_argument("--quest", type=int, action="append", help="restrict to quest ID(s)")
    parser.add_argument("--tables-only", action="store_true", help="skip synthesis, rebuild tables")
    parser.add_argument("--shard", metavar="I/N",
                        help="take every Nth file of the todo list (0/2 and 1/2 in two processes). "
                             "The GPU is only ~60%% busy on one autoregressive stream; two together "
                             "measured 2.28x realtime against 1.68x alone, three no better than two")
    parser.add_argument("--stale-only", action="store_true",
                        help="only regenerate files whose recorded text fingerprint no longer matches, "
                             "skipping lines that have no audio yet")
    parser.add_argument("--reindex", action="store_true",
                        help="record the current text's fingerprint for files that already exist, without "
                             "generating anything, so a later text change is detected (files made before the "
                             "fingerprint existed have none and are otherwise left alone)")
    parser.add_argument("--captured", action="store_true", help="only lines captured in game (not bulk sources)")
    parser.add_argument("--zone", type=int, action="append", help="only quests with this QuestSortID / AreaTable ID (from the client cache)")
    parser.add_argument("--assume-voice", help="voice for quests whose speaker is unknown, e.g. skyborne-male (default: skip them)")
    parser.add_argument("--narrator-voices", default="all", metavar="SPEC",
                        help="alternate narrator voices to generate: all (default), none, or a comma separated list")
    parser.add_argument("--cpu", action="store_true", help="generate on the CPU when there is no GPU (very slow; off by default so a lost GPU fails loudly)")
    parser.add_argument("--narrator-only", action="store_true",
                        help="generate only the alternate narrator voices, nothing else")
    args = parser.parse_args(argv)
    alternate_voices = parse_narrator_voices(args.narrator_voices)
    if args.narrator_only and not alternate_voices:
        raise SystemExit("--narrator-only with --narrator-voices none has nothing to do")

    capture = load_sources()
    if not capture["quests"] and not capture["gossip"]:
        print("nothing to voice: run ingest.py, classicdb.py or wdbcache.py first")
        return 1
    sound_index = json.loads(SOUND_INDEX.read_text(encoding="utf-8")) if SOUND_INDEX.exists() else {}
    items = load_items(capture, include_progress=args.progress)

    todo: list[Target] = []
    skipped: dict[str, int] = {}
    dirty: set[str] = set()   # index keys this process wrote since its last save

    stale: set[str] = set()

    def wanted(target: Target) -> bool:
        """False when the file is already there in the voice it should be in."""
        if not target.path.exists() or args.force:
            return True
        recorded = sound_index.get(target.key)
        previous_text = recorded.get("t") if isinstance(recorded, dict) else None
        if previous_text is not None and previous_text != target.fingerprint:
            skipped["text changed, regenerating"] = skipped.get("text changed, regenerating", 0) + 1
            stale.add(target.key)
            return True
        previous_voice = recorded.get("v") if isinstance(recorded, dict) else None
        if previous_voice is None or previous_voice == target.voice or target.voice == args.assume_voice:
            return False
        skipped["voice changed, regenerating"] = skipped.get("voice changed, regenerating", 0) + 1
        return True

    for item in items:
        if args.only and item.kind != args.only:
            continue
        if args.captured and not item.entry.get("player"):
            continue
        if args.zone and item.entry.get("sortID") not in args.zone:
            continue
        if args.quest and (item.kind != "quests" or int(item.entry["questID"]) not in args.quest):
            continue
        if item.entry.get("found") and not args.all and item.entry.get("pack") != "Forever":
            skipped["covered by another pack"] = skipped.get("covered by another pack", 0) + 1
            continue
        if item.kind == "gossip" and speaker_int(item.speaker_key) is None:
            skipped["no speaker id"] = skipped.get("no speaker id", 0) + 1
            continue
        if item.kind == "quests" and item.speaker_key is None and not item.entry.get("isObject"):
            # Cache-only quest whose giver we have not met: wait for a capture so it gets the right
            # voice, unless the caller vouches for a voice (e.g. a single-race starting zone)
            if not args.assume_voice:
                skipped["speaker unknown (play it to capture)"] = skipped.get("speaker unknown (play it to capture)", 0) + 1
                continue
            item.voice = args.assume_voice
        for variant in item.variants():
            base, text = variant.base, variant.text
            candidates: list[Target] = []
            if not is_speakable(text):
                skipped["unresolved markup"] = skipped.get("unresolved markup", 0) + 1
            else:
                if not args.narrator_only:
                    candidates.append(Target(item, base, text, item.voice))
                if item.is_narrator:
                    candidates += [Target(item, base, text, voice, True) for voice in alternate_voices]
            if not item.is_narrator and not args.dry_run and not args.reindex:
                # A speaker that has gained a voice of its own (a species clip
                # built, a capture naming the giver) leaves whole-line alternate
                # narrator recordings behind. rebuild_tables no longer lists them,
                # but they would still be probed into the index and shipped, and
                # the line's own file is about to be regenerated anyway.
                for voice in NARRATOR_VOICES[1:]:
                    leftover = sound_path(item.subfolder, base, voice)
                    if leftover.exists():
                        leftover.unlink()
                        sound_index.pop(index_key(base, voice), None)
                        dirty.add(index_key(base, voice))
                        skipped["stale narrator alternate removed"] = skipped.get("stale narrator alternate removed", 0) + 1
            # A line that mixes the speaker and the narrator: the speaker's parts
            # in their voice, each <stage direction> in the narrator's, and the
            # narrator's parts again in every alternate narrator voice.
            for index, (role, words) in enumerate(variant.parts, 1):
                if not is_speakable(words):
                    skipped["unresolved markup"] = skipped.get("unresolved markup", 0) + 1
                    continue
                part = part_name(base, index)
                if role == "npc":
                    if not args.narrator_only:
                        candidates.append(Target(item, part, words, item.voice))
                    continue
                if not args.narrator_only:
                    candidates.append(Target(item, part, words, NARRATOR_VOICE))
                candidates += [Target(item, part, words, voice, True) for voice in alternate_voices]
            if args.reindex:
                for target in candidates:
                    recorded = sound_index.get(target.key)
                    if isinstance(recorded, dict) and target.path.exists():
                        recorded["t"] = target.fingerprint
                        dirty.add(target.key)
                continue
            todo.extend(target for target in candidates if wanted(target))
    if args.reindex:
        save_sound_index(sound_index, dirty)
        stamped = sum(1 for value in sound_index.values() if isinstance(value, dict) and value.get("t"))
        print(f"reindexed: {stamped} of {len(sound_index)} entries now carry a text fingerprint")
        return 0

    if args.stale_only:
        todo = [target for target in todo if target.key in stale]

    if args.shard:
        index, _, count = args.shard.partition("/")
        index, count = int(index), int(count)
        if not 0 <= index < count:
            raise SystemExit(f"--shard {args.shard}: index must be 0..{count - 1}")
        # Interleave rather than split in half: both workers then follow the same
        # priority order, so stopping early still leaves the low-level zones done.
        todo = todo[index::count]

    # Low-level content first so early zones are playable soonest, and every line
    # in its own voice before any alternate, so a time-boxed run still adds breadth
    todo.sort(key=lambda t: (t.alternate, t.item.kind != "quests", t.item.entry.get("level") or 0, t.base, t.voice))
    if args.limit:
        todo = todo[: args.limit]

    narrator_count = sum(1 for target in todo if target.alternate)
    voices_needed = sorted({target.voice for target in todo})
    missing_voices = [v for v in voices_needed if not (VOICES_DIR / f"{v}.wav").exists()]
    print(f"{len(todo)} files to generate ({narrator_count} alternate narrator); skipped: {skipped or 'none'}")
    print(f"voices needed: {voices_needed}")
    if missing_voices:
        print(f"no reference clip for: {missing_voices} (falling back to narrator.wav / built-in voice)")

    if args.dry_run:
        for target in todo[:50]:
            print(f"  {target.label}  [{target.voice}]  {target.text[:90]}…")
        if len(todo) > 50:
            print(f"  … and {len(todo) - 50} more")
        return 0

    if todo and not args.tables_only:
        synth = Synth(allow_cpu=args.cpu)
        started = time.time()
        for n, target in enumerate(todo, 1):
            item = target.item
            t0 = time.time()
            duration = synth.speak(target.text, target.voice, target.path)
            sound_index[target.key] = {"d": duration, "v": target.voice, "t": target.fingerprint}
            dirty.add(target.key)
            print(f"[{n}/{len(todo)}] {target.label} {duration:5.1f}s audio in {time.time() - t0:4.1f}s  [{target.voice}] {item.entry.get('title') or item.entry.get('name')}")
            if n % 25 == 0:
                # Keep the pack tables current so a client restart picks up what exists so far.
                # Sources are reloaded so files made by another run (e.g. a --captured pass) are included.
                rebuild_tables(load_items(load_sources(), include_progress=True), sound_index, dirty=dirty)
        print(f"generated {len(todo)} files in {(time.time() - started) / 60:.1f} min")

    rebuild_tables(load_items(load_sources(), include_progress=True), sound_index, dirty=dirty)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
