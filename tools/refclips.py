"""Audition a voice's candidate clips and pick the reference by ear.

Composing a reference automatically - longest clip first, a rotating slice of the
emote speech - fills the window without listening to what goes in it, and the client's
greeting kits are not all conversational: a set's longest line is often a shout, a
death cry or a combat taunt, and that becomes the voice (#41). This walks the
candidates instead, so the clips are chosen rather than sorted into place.

    ./tools/run.sh fvo-refclips list bloodelf-female          # what is available
    ./tools/run.sh fvo-refclips labels                        # spoken numbers, once (GPU)
    ./tools/run.sh fvo-refclips reel bloodelf-female          # numbered audio to listen to
    ./tools/run.sh fvo-refclips build bloodelf-female 4,11,2  # that order becomes the clip

`build` prints the FileDataIDs to paste into forever-vo.toml, which is what makes the
choice survive the next rebuild:

    [voices.sources.bloodelf-female]
    clips = [539161, 539165, ...]

Numbers are positions in `list`/`reel` for this voice and are stable as long as the
client build is - the TOML records FileDataIDs, not positions, so a wago update cannot
silently repoint a pick at different audio.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import requests

from tools.build_retail_references import is_combat
from tools.build_voice_references import (
    RAW_DIR,
    S3GEN_SECONDS,
    SPEECH_EMOTES,
    T3_SECONDS,
    base_voice,
    build_picked_reference,
    duration,
    emote_speech_fdids,
    files_by_kit,
    named_npc_fdids,
    set_fdids,
)
from tools.config import (
    BETA_BUILD,
    GENDER_DICT,
    RACE_DICT,
    RETAIL_BUILD,
    VOICES_DIR,
    Voices,
    load_config,
)
from tools.soundpaths import folders, named_folder_files
from tools.wowdata import (
    display_sound_set,
    fetch_file,
    is_dud,
    load_db2,
    sound_set_displays,
)

LABELS_DIR = VOICES_DIR / "audition-labels"
LABEL_VOICE = "human-female"
GAP_SECONDS = 0.5


class Candidate:
    """One clip a voice could be built from."""

    def __init__(self, kind: str, fdid: int, build: str, group: str = "") -> None:
        self.kind, self.fdid, self.build = kind, fdid, build
        self.group = group or kind  # what the audition page's folder select shows
        self.path = Path()
        self.seconds = 0.0

    def fetch(self, voice: str) -> bool:
        """False when this clip cannot be had, rather than taking the voice down with it.

        wago answers 504 often enough on a long run (gnome-male dies on one file out of
        dozens), and a clip that will not download or will not probe is one candidate
        missing from a list, not a reason to offer none of them.
        """
        try:
            self.path = fetch_file(
                self.fdid, RAW_DIR / voice / f"{self.fdid}.ogg", build=self.build
            )
            if is_dud(self.path):  # named in the listfile, not carried by this build
                return False
            self.seconds = duration(self.path)
        except (
            FileNotFoundError,
            requests.RequestException,
            subprocess.CalledProcessError,
            OSError,
        ) as e:
            print(f"skip {self.fdid}: {e}", file=sys.stderr)
            return False
        return self.seconds > 0


def emote_names(build: str) -> dict[int, str]:
    return {int(key): row["Name"] for key, row in load_db2("EmotesText", build).items()}


def speech_candidates(voice: str) -> list[Candidate]:
    """This client's spoken emotes, or retail's for a race Classic never voiced.

    Keyed on the race and gender, so an archetype offers its race's speech: emote
    recordings are per race, not per NPCSounds set. Looking the archetype's own name up
    found nothing and reported "no candidate clips" for all 49 of them.
    """
    race_gender = base_voice(voice)
    for build in (BETA_BUILD, RETAIL_BUILD):
        fdids = emote_speech_fdids(build).get(race_gender)
        if not fdids:
            continue
        kits, names = files_by_kit(build), emote_names(build)
        kind_of: dict[int, str] = {}
        for row in load_db2("EmotesTextSound", build).values():
            race = RACE_DICT.get(int(row.get("RaceID") or 0))
            gender = GENDER_DICT.get(int(row.get("SexID") or 0))
            if f"{race}-{gender}" != race_gender:
                continue
            name = names.get(int(row.get("EmotesTextID") or 0))
            if name in SPEECH_EMOTES:
                for fdid in kits.get(int(row.get("SoundID") or 0), []):
                    kind_of[fdid] = name.lower()
        where = "speech" if build == BETA_BUILD else "retail speech"
        return [
            Candidate(f"{where} {kind_of.get(f, '')}".strip(), f, build, where)
            for f in fdids
        ]
    return []


def _bark_last(folder: str, stem: str) -> bool:
    """A swing, shout or death cry, to list after the lines that talk.

    is_combat reads the underscore tokens of a vo_ name; a Classic name runs the
    character into the kind (sylvanaswindrunneraggro02), so it is also asked about what
    is left once the folder's name is taken off the front.
    """
    rest = stem[len(folder) :] if stem.startswith(folder) else ""
    return is_combat(stem) or bool(rest and is_combat(rest.strip("_")))


def named_candidates(voice: str, voices: Voices) -> list[Candidate]:
    """A named NPC's greeting kit, then every line in its set's sound folder and in the
    folders `[voices.named_folders]` adds, each folder a group, barks last.

    The kit is the four greetings Sylvanas's NPCSounds row links; her folder holds about
    ninety more lines the client ships (tools/soundpaths.py, `--folders`).
    """
    found = [
        Candidate("greeting", fdid, BETA_BUILD, "greetings")
        for fdid in named_npc_fdids().get(voice, [])
    ]
    try:
        own = folders().get(display_sound_set(int(voice.removeprefix("npc-"))) or 0)
    except ValueError:
        own = None
    if own:
        files = named_folder_files()
        for folder in [own, *voices.named_folders.get(own, [])]:
            rows = files.get(folder.lower(), [])
            rows = sorted(rows, key=lambda r: (_bark_last(folder.lower(), r[1]), r[0]))
            found.extend(
                Candidate(stem, fdid, BETA_BUILD, folder) for fdid, stem, _ in rows
            )
    return found


def candidates(voice: str, voices: Voices | None = None) -> list[Candidate]:
    """Speech first, then greetings. Fetches as it goes.

    An archetype offers its own set's greetings and its race's speech; a plain voice
    offers every set the race is cast with, most-used first. A named NPC
    (`npc-<displayID>`) has no race, so none of that finds anything: it offers its
    greeting kit and its sound folders instead (named_candidates).
    """
    if voice.startswith("npc-"):
        return _fetched(named_candidates(voice, voices or load_config().voices), voice)
    found = speech_candidates(voice)
    race_gender = base_voice(voice)
    counts: Counter[int] = sound_set_displays().get(race_gender) or Counter()
    own = re.fullmatch(r".+-s(\d+)", voice)
    wanted_sets = (
        [(int(own.group(1)), counts.get(int(own.group(1)), 0))]
        if own
        else counts.most_common()
    )
    for sound_id, displays in wanted_sets:
        for fdid in set_fdids(sound_id):
            found.append(
                Candidate(
                    f"set {sound_id} ({displays} displays)",
                    fdid,
                    BETA_BUILD,
                    f"set {sound_id}",
                )
            )
    return _fetched(found, voice)


def _fetched(found: list[Candidate], voice: str) -> list[Candidate]:
    """The first of each FileDataID that can be had, in order."""
    seen: set[int] = set()
    kept = []
    for candidate in found:
        if candidate.fdid in seen or not candidate.fetch(voice):
            continue
        seen.add(candidate.fdid)
        kept.append(candidate)
    return kept


def cmd_list(args) -> int:
    found = candidates(args.voice)
    if not found:
        print(f"{args.voice}: no candidate clips")
        return 1
    print(f"{'#':>4}  {'group':<20} {'source':<36} {'fdid':>9}  seconds")
    for i, c in enumerate(found, 1):
        print(
            f"{i:>4}  {c.group[:20]:<20} {c.kind[:36]:<36} {c.fdid:>9}  {c.seconds:5.1f}"
        )
    print(f"\n{len(found)} clips, {sum(c.seconds for c in found):.0f}s total")
    return 0


def cmd_labels(args) -> int:
    """Spoken numbers for the reel. Beeps could not be counted and espeak was unintelligible."""
    import perth  # pyright: ignore[reportMissingImports]
    import torchaudio  # pyright: ignore[reportMissingImports]

    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        perth.PerthImplicitWatermarker = perth.DummyWatermarker  # ty: ignore[invalid-assignment]
    from chatterbox.tts import ChatterboxTTS  # pyright: ignore[reportMissingImports]

    ones = [
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    ]
    tens = {
        2: "twenty",
        3: "thirty",
        4: "forty",
        5: "fifty",
        6: "sixty",
        7: "seventy",
        8: "eighty",
        9: "ninety",
    }

    def spoken(n: int) -> str:
        if n < 20:
            return ones[n - 1]
        word = tens[n // 10]
        return word if n % 10 == 0 else f"{word}-{ones[n % 10 - 1]}"

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    todo = [n for n in range(1, args.count + 1) if not (out / f"{n:03d}.wav").exists()]
    if not todo:
        print(f"{args.count} labels already in {out}")
        return 0
    model = ChatterboxTTS.from_pretrained(device=args.device)
    for n in todo:
        wav = model.generate(
            f"{spoken(n)}.",
            exaggeration=0.4,
            cfg_weight=0.5,
            audio_prompt_path=str(VOICES_DIR / f"{LABEL_VOICE}.wav"),
        ).cpu()
        torchaudio.save(str(out / f"{n:03d}.wav"), wav, model.sr)
        print(f"label {n}", flush=True)
    return 0


def cmd_reel(args) -> int:
    found = candidates(args.voice)
    labels = Path(args.labels)
    missing = [
        i for i in range(1, len(found) + 1) if not (labels / f"{i:03d}.wav").exists()
    ]
    if missing:
        print(
            f"no spoken label for {len(missing)} of {len(found)} clips; "
            f"run `fvo-refclips labels --count {len(found)}` first"
        )
        return 1
    out = Path(args.out or VOICES_DIR / "audition" / f"{args.voice}.mp3")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / f".{args.voice}-work"
    work.mkdir(parents=True, exist_ok=True)
    gap = work / "gap.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-t",
            str(GAP_SECONDS),
            "-i",
            "anullsrc=r=24000:cl=mono",
            str(gap),
        ],
        check=True,
    )
    lines = []
    for i, c in enumerate(found, 1):
        clip = work / f"{i:03d}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(c.path),
                "-ar",
                "24000",
                "-ac",
                "1",
                "-af",
                "loudnorm",
                str(clip),
            ],
            check=True,
        )
        for part in ((labels / f"{i:03d}.wav"), gap, clip, gap):
            lines.append(f"file '{part.resolve()}'\n")
    listing = work / "concat.txt"
    listing.write_text("".join(lines), encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(out),
        ],
        check=True,
    )
    cmd_list(args)
    print(f"\nreel: {out}")
    return 0


def cmd_build(args) -> int:
    found = candidates(args.voice)
    try:
        picks = [found[int(n) - 1] for n in args.clips.split(",") if n.strip()]
    except (ValueError, IndexError):
        print(f"pick numbers between 1 and {len(found)}, comma separated")
        return 1
    build_picked_reference(args.voice, [c.path for c in picks])
    window = 0.0
    for c in picks:
        mark = (
            "t3"
            if window < T3_SECONDS
            else ("s3gen" if window < S3GEN_SECONDS else "averaged only")
        )
        print(f"  {window:5.1f}s  {c.kind:<28} {c.fdid:>9}  {c.seconds:5.1f}s  {mark}")
        window += c.seconds
    print(
        f"\n[voices.sources.{args.voice}]\nclips = [{', '.join(str(c.fdid) for c in picks)}]"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    lister = sub.add_parser("list", help="numbered table of a voice's candidate clips")
    lister.add_argument("voice")
    lister.set_defaults(func=cmd_list)

    labeller = sub.add_parser(
        "labels", help="generate the spoken numbers a reel needs (GPU)"
    )
    labeller.add_argument("--count", type=int, default=60)
    labeller.add_argument("--out", default=str(LABELS_DIR))
    labeller.add_argument("--device", default="cuda")
    labeller.set_defaults(func=cmd_labels)

    reel = sub.add_parser(
        "reel", help="numbered audio of every candidate, to listen through"
    )
    reel.add_argument("voice")
    reel.add_argument("--out")
    reel.add_argument("--labels", default=str(LABELS_DIR))
    reel.set_defaults(func=cmd_reel)

    build = sub.add_parser(
        "build", help="build the reference from chosen numbers, in that order"
    )
    build.add_argument("voice")
    build.add_argument(
        "clips", help="comma separated positions from `list`, head first"
    )
    build.set_defaults(func=cmd_build)

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
