"""Generates missing voice lines with a local TTS model and rebuilds the
ForeverVO_Data voice pack tables.

    ./tools/run.sh python tools/generate.py --dry-run          # what would be generated
    ./tools/run.sh python tools/generate.py --limit 20         # generate a few
    ./tools/run.sh python tools/generate.py                    # everything missing
    ./tools/run.sh python tools/generate.py --tables-only      # just rebuild the pack tables

Input is tools/data/capture.json (see ingest.py). Audio goes to
ForeverVO_Data/Sounds/{Quests,Gossip}/ and the tables to ForeverVO_Data/Data/.
Tables are rebuilt from scratch every run from capture.json plus the files that
exist on disk, so the pack always matches what is actually present.

Voices: tools/voices/<race>-<gender>.wav are reference clips for cloning
(build_voice_references.py makes them from the client's own audio). Missing
voices fall back to narrator.wav, then to the model's built-in voice.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from config import CAPTURE_JSON, DATA_DIR, FALLBACK_VOICES, PACK_DATA_DIR, SOUND_INDEX, SOUNDS_DIR, VOICES_DIR
from luatable import lua_string
from textclean import chunk, clean, has_gender_branch, is_speakable, split_gender
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
        self.voice = voice_for_npc(npc)
        self.raw_text = entry.get("text") or ""
        self.event = entry.get("event") or "gossip"
        self.speaker_key = entry.get("npc")   # "288" or "-123" (game object)

    @property
    def subfolder(self) -> str:
        return "Quests" if self.kind == "quests" else "Gossip"

    @property
    def hash(self) -> str:
        return text_key(self.raw_text, self.entry.get("player"))

    @property
    def base_name(self) -> str:
        if self.kind == "quests":
            return f"{int(self.entry['questID'])}-{self.event}"
        speaker = self.speaker_key or "unknown"
        speaker = speaker.replace("-", "obj")
        return f"{speaker}-{self.hash}"

    @property
    def gendered(self) -> bool:
        return has_gender_branch(clean(self.raw_text))

    def variants(self) -> list[tuple[str, str]]:
        """(file base name, text to speak); two when the text branches on player gender."""
        text = clean(self.raw_text)
        if has_gender_branch(text):
            male, female = split_gender(text)
            return [(f"m-{self.base_name}", male), (f"f-{self.base_name}", female)]
        return [(self.base_name, text)]


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
                    if value is not None and value != "":
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
    def __init__(self, device: str = "cuda"):
        import perth
        import torch
        if getattr(perth, "PerthImplicitWatermarker", None) is None:
            perth.PerthImplicitWatermarker = perth.DummyWatermarker
        from chatterbox.tts import ChatterboxTTS
        self.torch = torch
        self.model = ChatterboxTTS.from_pretrained(device=device if torch.cuda.is_available() else "cpu")
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
            wav = self.model.generate(part, exaggeration=0.45, cfg_weight=0.5, **kwargs)
            pieces.append(wav.cpu())
            pieces.append(silence)
        audio = self.torch.cat(pieces[:-1], dim=-1)
        duration = audio.shape[-1] / self.sr

        import torchaudio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            torchaudio.save(str(tmp_path), audio, self.sr)
            out_mp3.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(tmp_path), "-ac", "1", "-ar", "44100",
                 "-codec:a", "libmp3lame", "-q:a", "4", str(out_mp3)],
                check=True,
            )
        finally:
            tmp_path.unlink(missing_ok=True)
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
    raise TypeError(type(value))


def lua_record(fields: dict) -> str:
    parts = [f"{k}={lua_value(v)}" for k, v in fields.items() if v is not None and v is not False]
    return "{ " + ", ".join(parts) + " }"


def write_table(filename: str, field: str, lines: list[str]) -> None:
    PACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines)
    (PACK_DATA_DIR / filename).write_text(
        f"-- Generated by tools/generate.py; do not edit.\nlocal pack = ForeverVO_DataPack\npack.{field} = {{\n{body}\n}}\n",
        encoding="utf-8",
    )


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def speaker_int(key: str | None) -> int | None:
    try:
        return int(key) if key is not None else None
    except ValueError:
        return None


def rebuild_tables(items: list[Item], sound_index: dict[str, float]) -> None:
    present = {p.stem for p in SOUNDS_DIR.glob("*/*.mp3")}
    for name in present:
        if name not in sound_index:
            folder = "Quests" if name.lstrip("mf-").split("-")[0].isdigit() and "-" in name.lstrip("mf-") else "Gossip"
            sound_index[name] = probe_duration(SOUNDS_DIR / folder / f"{name}.mp3")

    quests: dict[int, dict] = {}
    gossip: dict[int, list[dict]] = {}
    npcs: dict[int, str] = {}

    for item in items:
        variants = item.variants()
        available = [base for base, _ in variants if base in present]
        if not available:
            continue
        gendered = len(variants) == 2
        duration = max(sound_index.get(base, 0.0) for base in available)
        speaker = speaker_int(item.speaker_key)
        name = item.entry.get("name") or (item.npc or {}).get("name")
        if speaker is not None and name:
            npcs[speaker] = name

        if item.kind == "quests":
            quest_id = int(item.entry["questID"])
            record = quests.setdefault(quest_id, {})
            record[QUEST_EVENTS[item.event]] = round(duration, 3)
            if gendered:
                record["g"] = True
            if speaker is not None and record.get("npc") is None:
                record["npc"] = speaker
        else:
            if speaker is None:
                continue
            gossip.setdefault(speaker, []).append({
                "f": item.base_name,
                "h": item.hash,
                "t": item.raw_text.replace("\r", " ").replace("\n", " "),
                "d": round(duration, 3),
                "g": gendered or None,
            })

    quest_lines = [f"\t[{qid}] = {lua_record(rec)}," for qid, rec in sorted(quests.items())]
    gossip_lines = []
    for speaker, entries in sorted(gossip.items()):
        gossip_lines.append(f"\t[{speaker}] = {{")
        for entry in sorted(entries, key=lambda e: e["f"]):
            gossip_lines.append(f"\t\t{lua_record(entry)},")
        gossip_lines.append("\t},")
    npc_lines = [f"\t[{key}] = {lua_string(name)}," for key, name in sorted(npcs.items())]

    write_table("Quests.lua", "quests", quest_lines)
    write_table("Gossip.lua", "gossip", gossip_lines)
    write_table("NPCs.lua", "npcs", npc_lines)
    SOUND_INDEX.parent.mkdir(parents=True, exist_ok=True)
    SOUND_INDEX.write_text(json.dumps(sound_index, indent=1, sort_keys=True), encoding="utf-8")
    print(f"pack tables: {len(quests)} quests, {sum(len(v) for v in gossip.values())} gossip lines, "
          f"{len(npcs)} speakers, {len(present)} sound files")


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
    args = parser.parse_args(argv)

    capture = load_sources()
    if not capture["quests"] and not capture["gossip"]:
        print("nothing to voice: run ingest.py, classicdb.py or wdbcache.py first")
        return 1
    sound_index = json.loads(SOUND_INDEX.read_text(encoding="utf-8")) if SOUND_INDEX.exists() else {}
    items = load_items(capture, include_progress=args.progress)

    todo: list[tuple[Item, str, str]] = []
    skipped: dict[str, int] = {}
    for item in items:
        if args.only and item.kind != args.only:
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
            # Cache-only quest whose giver we have not met: wait for a capture so it gets the right voice
            skipped["speaker unknown (play it to capture)"] = skipped.get("speaker unknown (play it to capture)", 0) + 1
            continue
        for base, text in item.variants():
            out = SOUNDS_DIR / item.subfolder / f"{base}.mp3"
            if out.exists() and not args.force:
                continue
            if not is_speakable(text):
                skipped["unresolved markup"] = skipped.get("unresolved markup", 0) + 1
                continue
            todo.append((item, base, text))
    # Low-level content first so early zones are playable soonest
    todo.sort(key=lambda t: (t[0].kind != "quests", t[0].entry.get("level") or 0, t[1]))
    if args.limit:
        todo = todo[: args.limit]

    voices_needed = sorted({item.voice for item, _, _ in todo})
    missing_voices = [v for v in voices_needed if not (VOICES_DIR / f"{v}.wav").exists()]
    print(f"{len(todo)} files to generate; skipped: {skipped or 'none'}")
    print(f"voices needed: {voices_needed}")
    if missing_voices:
        print(f"no reference clip for: {missing_voices} (falling back to narrator.wav / built-in voice)")

    if args.dry_run:
        for item, base, text in todo[:50]:
            print(f"  {item.subfolder}/{base}.mp3  [{item.voice}]  {text[:90]}…")
        if len(todo) > 50:
            print(f"  … and {len(todo) - 50} more")
        return 0

    if todo and not args.tables_only:
        synth = Synth()
        started = time.time()
        for n, (item, base, text) in enumerate(todo, 1):
            out = SOUNDS_DIR / item.subfolder / f"{base}.mp3"
            t0 = time.time()
            duration = synth.speak(text, item.voice, out)
            sound_index[base] = duration
            print(f"[{n}/{len(todo)}] {item.subfolder}/{base}.mp3 {duration:5.1f}s audio in {time.time() - t0:4.1f}s  [{item.voice}] {item.entry.get('title') or item.entry.get('name')}")
            if n % 25 == 0:
                # Keep the pack tables current so a client restart picks up what exists so far
                rebuild_tables(items, sound_index)
        print(f"generated {len(todo)} files in {(time.time() - started) / 60:.1f} min")

    rebuild_tables(items, sound_index)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
