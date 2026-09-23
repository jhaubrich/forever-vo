# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""Builds extra cloning references from retail World of Warcraft voice-over.

Some speakers have no usable source in the Forever client or in a VoiceOver pack.
The Skyborne are the worst case: they are new, so nothing predating Forever has
their voice, and all of them end up sharing the one reference built from the
client's player vocal lines. Retail has thousands of recorded elf and troll lines,
including generic NPC voice sets - a void elf civilian rather than Alleria, whose
voice is distinctive enough to distract - which is what a quest giver should sound
like.

    python tools/build_retail_references.py --dry-run
    python tools/build_retail_references.py --only skyborne-female

Files are fetched by FileDataID through wago.tools, so no CASC extraction is
needed. Output lands in tools/voices/<race>-<gender>-<label>.wav, which
build_voiceover_references.py then picks up as extra voices for that race.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

from config import DATA_DIR, VOICES_DIR, WAGO_BASE

RETAIL_BUILD = "12.1.0.69875"
LISTFILE = DATA_DIR / "verified-listfile.csv"
LISTFILE_URL = (
    "https://github.com/wowdev/wow-listfile/releases/download/"
    "202609211740/verified-listfile.csv"
)
RAW_DIR = VOICES_DIR / "raw-retail"
CANDIDATES = 14        # files downloaded per voice before picking
TARGET_SECONDS = 20.0
MIN_CLIP, MAX_CLIP = 2.0, 12.0   # a spoken line, not a grunt or a whole cinematic

# A creature's vo_ files are mostly combat: swings, crits, shouts and death cries.
# Those pass a duration filter happily and then get cloned, which is how a quest
# giver ends up sounding like she is screaming down a pipe. Several generic
# npc_-_* voice sets are 100% combat and contain no speech at all, so a source
# that yields nothing after this filter must be replaced, not re-filtered.
COMBAT = re.compile(
    r"_(attack|attackcrit|battleshout|death|aggro|wound|pissed|flee|taunt|jump|"
    r"fall|gasp|grunt|pain|spell|cast)\w*_?\d*\.ogg$", re.I)

# label -> creature directory under sound/creature/. Generic NPC sets first: they
# are ordinary voices, which is what most quest givers are.
SOURCES: dict[str, str | tuple[str, str]] = {
    # Sky elves: no pre-Forever recording exists, so retail elves stand in.
    # The generic npc_-_void_elf_* and nightborne_*_caster sets look ideal by name
    # and are a trap - every line in them is a swing, a shout or a death cry, with
    # no speech whatsoever. Named characters and the nightborne citizen/light/heavy
    # sets are the ones that actually talk.
    "skyborne-female-civilian": "nightborne_female_light",
    "skyborne-female-noble": "vereesa_windrunner",
    # The light/heavy/caster suffix on an NPC voice set is body and armour type, and
    # the heavy sets are the gruff armoured ones - cloned onto a village herbalist
    # they come out raspy. These labels are cosmetic anyway: speakers are shared
    # round-robin across the variants, so nothing keeps a "guard" voice away from a
    # civilian. Prefer sets that sound like someone talking to you.
    "skyborne-female-ranger": "alleria_windrunner",
    "skyborne-female-arcane": "first_arcanist_thalyssra",
    "skyborne-female-citizen": "nightborne_female_citizen",
    "skyborne-female-priest": "lady_liadrin",
    "skyborne-male-civilian": "nightborne_male_light",
    "skyborne-male-noble": "lorthemar",
    "skyborne-male-arcane": "magister_umbric",
    "skyborne-male-citizen": "nightborne_male_citizen",
    "skyborne-male-guard": "nightborne_male_heavy",
    "skyborne-male-ranger": "halduron_brightwing",
    # Darkspear country is not captured yet, so no speaker uses these. They are
    # built now because the troll models are shared with orcs: the moment someone
    # plays Sen'jin Village, those speakers need somewhere to go that is not
    # twenty seconds of stitched grunting.
    "troll-male-shadowhunter": "rokhan",
    "troll-male-chieftain": "voljin",
    "troll-male-loa": "bwonsamdi",
    "troll-female-princess": "princess_talanji",
    # Children. One folder holds both sexes, marked by an _m / _f suffix on the
    # file, so each voice takes only its own - a shared reference makes every boy
    # sound like a girl.
    "humanmalekid-male": ("kul_tiran_kid", r"_m\.ogg$"),
    "humanfemalekid-female": ("kul_tiran_kid", r"_f\.ogg$"),
    # Blood elves and humans are still on stitched barks.
    "bloodelf-female-generic": "genericdhbloodelffemale",
    "bloodelf-male-generic": "genericdhbloodelfmale",
    "human-female-generic": "taelia",
    "human-male-generic": "danath_trollbane",
}


def ensure_listfile() -> Path:
    if LISTFILE.exists():
        return LISTFILE
    LISTFILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading listfile -> {LISTFILE}")
    with requests.get(LISTFILE_URL, stream=True, timeout=600) as r:
        r.raise_for_status()
        with LISTFILE.open("wb") as f:
            for block in r.iter_content(1 << 20):
                f.write(block)
    return LISTFILE


def fdids_for(listfile: Path, source: str | tuple[str, str]) -> tuple[list[int], int]:
    """(speech FileDataIDs, how many combat lines were rejected).

    `source` is a creature folder, or (folder, pattern) when one folder holds more
    than one voice - kul_tiran_kid carries both sexes, marked _m and _f.
    """
    creature_dir, extra = (source, None) if isinstance(source, str) else source
    pattern = re.compile(
        rf"^(\d+);sound/creature/{re.escape(creature_dir)}/(vo_[^/]*\.ogg)$", re.IGNORECASE
    )
    within = re.compile(extra, re.IGNORECASE) if extra else None
    out, rejected = [], 0
    with listfile.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pattern.match(line.strip())
            if not m:
                continue
            if within and not within.search(m.group(2)):
                continue
            if COMBAT.search(m.group(2)):
                rejected += 1
                continue
            out.append(int(m.group(1)))
    return out, rejected


def fetch(fdid: int, dest: Path) -> Path | None:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(f"{WAGO_BASE}/api/casc/{fdid}", params={"version": RETAIL_BUILD}, timeout=180)
    except requests.RequestException as e:
        print(f"    fdid {fdid}: {e}")
        return None
    if r.status_code != 200 or r.headers.get("content-type", "").startswith("application/json"):
        return None
    dest.write_bytes(r.content)
    return dest


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def build(label: str, clips: list[Path]) -> Path:
    lst = RAW_DIR / label / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in clips), encoding="utf-8")
    dest = VOICES_DIR / f"{label}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-ac", "1", "-ar", "24000", "-af", "loudnorm", str(dest)],
        check=True,
    )
    return dest


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="resolve FileDataIDs, download nothing")
    ap.add_argument("--only", action="append", help="build just these labels (prefix match)")
    ap.add_argument("--candidates", type=int, default=CANDIDATES,
                    help="files to sample per voice; raise it when a source is mostly short barks")
    ap.add_argument("--force", action="store_true", help="rebuild even if the wav exists")
    args = ap.parse_args(argv)

    listfile = ensure_listfile()
    made = 0
    for label, creature in SOURCES.items():
        if args.only and not any(label.startswith(p) for p in args.only):
            continue
        fdids, rejected = fdids_for(listfile, creature)
        if not fdids:
            print(f"{label:28} NO SPEECH for sound/creature/{creature}/ "
                  f"({rejected} combat lines rejected) - pick another source")
            continue
        print(f"{label:28} {len(fdids):5} speech lines ({rejected} combat rejected) ({creature})")
        if args.dry_run:
            continue
        if (VOICES_DIR / f"{label}.wav").exists() and not args.force:
            print("    already built")
            continue
        clips: list[tuple[float, Path]] = []
        for fdid in fdids[:args.candidates]:
            p = fetch(fdid, RAW_DIR / label / f"{fdid}.ogg")
            if p:
                d = duration(p)
                if MIN_CLIP <= d <= MAX_CLIP:
                    clips.append((d, p))
        clips.sort(reverse=True)
        chosen, total = [], 0.0
        for d, p in clips:
            chosen.append(p)
            total += d
            if total >= TARGET_SECONDS:
                break
        if total < 8.0:
            print(f"    only {total:.1f}s usable, skipping")
            continue
        build(label, chosen)
        print(f"    -> {label}.wav  {len(chosen)} clips, {total:.1f}s")
        made += 1
    print(f"\n{made} reference clips written to {VOICES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
