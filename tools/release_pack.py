"""Builds and (optionally) uploads voice pack releases.

Four packs are released from the one working folder (ForeverVO_Data holds
everything on the maintainer's machine):

  base          ForeverVO_Data_Base          every line: quests to level 40
  base_endgame  ForeverVO_Data_Base_Endgame  with all gossip, and quests from
                                             41, split so each fits
                                             CurseForge's 1 GB website cap.
                                             Huge, rarely released, uploaded by
                                             hand. Building one records what
                                             it shipped (release_baseline.json).
                                             (The maintainer's working folder
                                             stays ForeverVO_Data; they coexist
                                             because their pack names differ.)
  delta         ForeverVO_Data_Forever       every line that differs from what
                                             the two Base packs shipped: new,
                                             re-worded, re-voiced, re-rolled.
                                             Small, released nightly, higher
                                             priority so it overrides the base.
                                             Until both Base packs have been
                                             built with a baseline, the old
                                             rule: lines read in game.
  base_books    ForeverVO_Data_Base_Books    every page of every book, letter
                                             and plaque, in every narrator
                                             voice. Its own project, since the
                                             Base packs are near the 1 GB cap.
                                             Until it has been built with a
                                             baseline, the delta carries only
                                             the pages read in game.

    ./tools/run.sh tools/release_pack.py delta               # build zip only
    ./tools/run.sh tools/release_pack.py delta --upload      # and upload to CurseForge
    ./tools/run.sh tools/release_pack.py delta --upload --if-changed   # nightly use
    ./tools/run.sh tools/release_pack.py base && ./tools/run.sh tools/release_pack.py base_endgame

Audio is re-encoded for release (mono 32 kbps mp3 at 22.05 kHz with no
Xing/Info header frame, about 14 MB per hour of speech; it was 48 kbps until
2026-09-22, when the base pack came to 1.3 GB with a third of the lines still
to go, and carried the header until 2026-09-25, when the client turned out to
misread it and stop every line at 32/56 of its length, see the comment at
SAMPLE_RATE) into tools/data/release/. The zip stores the files uncompressed,
since mp3 does not deflate. Versions are date based (2026.09.21, then
2026.09.21.2 on the same day). Packs upload as "release" files unless
--release-type says otherwise (the delta was "beta" until 2026-09-24: the
CurseForge app hides beta files unless the user opts in, so default installs
never got it). --if-changed compares the file set and the encoding with the
last release recorded in release_state.json, so a change to either releases
every file again.

The API key comes from the repo's .env (gitignored): CF_API_KEY=... (the name
the BigWigs packager uses too; CURSEFORGE_API_KEY is still accepted).
Project IDs, the level the base set splits at, the bitrate and the transcode
parallelism are [release] in forever-vo.toml.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import requests
from requests_toolbelt import MultipartEncoder

from tools.config import DATA_DIR, SOUND_INDEX, SOUNDS_DIR, Config, Release, load_config
from tools.generate import (
    Item,
    VoiceCatalog,
    load_items,
    load_sources,
    part_name,
    rebuild_tables,
    sound_folder,
)

RELEASE_DIR = DATA_DIR / "release"
STATE_FILE = DATA_DIR / "release_state.json"
# What every file the Base packs last shipped sounded like ({pack: {name: stamp}}),
# written when a Base pack is built; the delta is whatever differs from it.
BASELINE_FILE = DATA_DIR / "release_baseline.json"
BASE_PACKS = ("base", "base_endgame")
BOOKS_PACK = "base_books"  # its own baseline: book pages are judged against it alone
CF_API = "https://wow.curseforge.com/api"
GAME_VERSION_NAME = "1.60.1"

# Release files carry no Xing/Info header frame (-write_xing 0). LAME puts one
# in front of a CBR stream, sized for the tag rather than the stream (56 kbps
# on a 32 kbps mono file), and the client's decoder does not recognise the
# CBR "Info" variant: it takes that first frame's bitrate as the file's and
# computes the length from the byte count, so until 2026-09-25 every line
# stopped at 32/56 of its length (958-accept, 31 s, stopped at 16 s; reported
# on all three CurseForge packs). Without the header the first frame is a
# real 32 kbps frame and the estimate is exact. The generator's VBR originals
# under ForeverVO_Data/Sounds carry the "Xing" variant, which the client does
# read, so the owner never heard it in play. Tested in game with the same
# line at 22.05 and 44.1 kHz, with and without the header. The sample rate
# was never a factor; 22.05 kHz keeps the most bandwidth per bit.
SAMPLE_RATE = 22050


def is_forever_line(entry: dict) -> bool:
    """Delta pack membership before the first Base build that records a baseline:
    lines players saw in game. Everything else is Classic's text and stays in the
    base pack, so the delta does not grow as the bulk run works through Classic.

    It grew anyway, with play: every quest a player reads is captured, and a
    Classic line read in game moved to the delta with the same audio (half of its
    178 MB on 2026-09-29, and 10 MB shipped in both). See `delta_line`."""
    source = entry.get("source", "classic")
    return bool(entry.get("player")) or source in ("capture", "community")


def file_stamp(index: dict, name: str) -> str:
    """What a pack file says, from sound_index: text fingerprint, voice, duration
    (the duration gives away a re-rolled take, #333). `name` is a base name, or a
    path under Sounds/ for an alternate (Quests/Narrator/<voice>/<base>,
    Gossip/Sex/<m|f>/<base>), whose index key drops the Quests/Gossip folder."""
    if "/" in name:
        name = name.split("/", 1)[1]
    entry = index.get(name)
    if not isinstance(entry, dict):
        return "?"
    return f"{entry.get('t') or '?'}:{entry.get('v') or '?'}:{entry.get('d') or '?'}"


def line_files(item: Item, config: Config) -> list[str]:
    """Every file a line can have, named as rebuild_tables lists them: its gender
    variants, their parts, the alternate narrators' recordings of whatever the
    narrator reads, and the speaker's other sex."""
    names: list[str] = []
    folder = item.subfolder
    voices = config.voices.narrator_alternates
    for variant in item.variants():
        names.append(variant.base)
        if item.is_narrator:
            names += [f"{folder}/Narrator/{v}/{variant.base}" for v in voices]
        for index, (role, _) in enumerate(variant.parts, 1):
            part = part_name(variant.base, index)
            names.append(part)
            if role == "narrator":
                names += [f"{folder}/Narrator/{v}/{part}" for v in voices]
        if item.sex_alternate:
            names.append(f"{folder}/Sex/{item.sex_alternate[0]}/{variant.base}")
    return names


def on_disk(name: str, sounds_dir: Path = SOUNDS_DIR) -> bool:
    if "/" in name:
        return (sounds_dir / f"{name}.mp3").exists()
    return (sounds_dir / sound_folder(name) / f"{name}.mp3").exists()


def delta_line(
    item: Item,
    config: Config,
    baseline: dict[str, str],
    index: dict,
    sounds_dir: Path = SOUNDS_DIR,
) -> bool:
    """Delta pack membership: a line any of whose files is new since the Base packs
    were built, or says something else now (text, voice, a new take), or is gone.
    A line the Base packs already carry as it is stays out, however many players
    have read it; the next Base release absorbs the rest and the delta starts over."""
    for name in line_files(item, config):
        was = baseline.get(name)
        if not on_disk(name, sounds_dir):
            if was is not None:
                return True  # shipped, and gone since (a line now only parts)
            continue
        if was is None or file_stamp(index, name) != was:
            return True
    return False


def load_baseline(packs: tuple[str, ...] = BASE_PACKS) -> dict[str, str] | None:
    """Every file the given packs last shipped, with its stamp; None until all of
    them have been built since baselines were recorded (one alone would put the
    other's whole set in the delta). Quests and gossip are judged against both
    Base packs, book pages against Base Books (load_baseline((BOOKS_PACK,)))."""
    if not BASELINE_FILE.exists():
        return None
    recorded = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    if not all(pack in recorded for pack in packs):
        return None
    merged: dict[str, str] = {}
    for pack in packs:
        merged.update(recorded[pack])
    return merged


def delta_member(
    item: Item,
    config: Config,
    baseline: dict[str, str] | None,
    book_baseline: dict[str, str] | None,
    index: dict,
    sounds_dir: Path = SOUNDS_DIR,
) -> bool:
    """Whether a line belongs in the delta: `delta_line` against the baseline of
    the pack that carries its kind, or while that pack has none, the old rule
    (lines read in game). Book pages have their own pack, so until Base Books is
    built the whole Classic set of pages stays out of the delta, which would
    otherwise grow past delta_cap_mb at once."""
    chosen = book_baseline if item.kind == "books" else baseline
    if chosen is None:
        return is_forever_line(item.entry)
    return delta_line(item, config, chosen, index, sounds_dir)


def record_baseline(pack: str, stats: dict, index_path: Path = SOUND_INDEX) -> None:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    packs = (
        json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        if BASELINE_FILE.exists()
        else {}
    )
    packs[pack] = {name: file_stamp(index, name) for name in pack_files(stats)}
    BASELINE_FILE.write_text(json.dumps(packs, indent=0, sort_keys=True), "utf-8")


def pack_files(stats: dict) -> list[str]:
    return (
        sorted(stats["files"])
        + sorted(stats.get("narratorFiles", ()))
        + sorted(stats.get("sexFiles", ()))
    )


def base_part(entry: dict, split_level: int) -> int:
    """1 for the Base pack, 2 for Base Endgame. The Classic set with its alternate
    narrators does not fit CurseForge's 1 GB cap in one file, so it is cut by
    quest level ([release] base_split_level); a quest's alternates must sit in the
    same pack as the quest, since the addon looks them up in the pack that had
    the entry, so the cut cannot be by anything finer."""
    if entry.get("questID") and int(entry.get("level") or 0) > split_level:
        return 2
    return 1


def is_book(item: Item) -> bool:
    """Book pages ship in Base Books, never in Base or Base Endgame: those are
    near CurseForge's 1 GB cap, and the pages in every narrator voice are about
    230 MB."""
    return item.kind == "books"


@dataclass(frozen=True)
class PackSpec:
    folder: str  # the addon folder the pack installs as; its global is <folder>Pack
    title: str
    pack_name: str  # what the addon shows as the pack's name
    priority: int  # a higher pack's line wins over a lower one's
    notes: str
    select: Callable[[Item], bool]  # line -> belongs to this pack


PACK_NAMES = ("base", "base_endgame", "base_books", "delta")


def pack_specs(release: Release) -> dict[str, PackSpec]:
    split = release.base_split_level
    return {
        "base": PackSpec(
            folder="ForeverVO_Data_Base",
            title="Forever Voiceover Data: Base",
            pack_name="Classic",
            priority=100,
            notes=f"Quests to level {split} and all gossip, voiced. Install with Forever Voiceover and Base Endgame.",
            select=lambda item: not is_book(item) and base_part(item.entry, split) == 1,
        ),
        "base_endgame": PackSpec(
            folder="ForeverVO_Data_Base_Endgame",
            title="Forever Voiceover Data: Base Endgame",
            pack_name="Classic Endgame",
            priority=100,
            notes=f"Quests from level {split + 1}, voiced. Install with Forever Voiceover and Base.",
            select=lambda item: not is_book(item) and base_part(item.entry, split) == 2,
        ),
        "base_books": PackSpec(
            folder="ForeverVO_Data_Base_Books",
            title="Forever Voiceover Data: Base Books",
            pack_name="Classic Books",
            priority=100,
            notes="Books, letters and plaques, read by the narrator. Install with Forever Voiceover.",
            select=is_book,
        ),
        "delta": PackSpec(
            folder="ForeverVO_Data_Forever",
            title="Forever Voiceover Data: Forever",
            pack_name="Forever",
            priority=200,
            notes="Lines new or revised since the Base packs, updated nightly. Sits on top of Forever Voiceover Data.",
            select=lambda item: is_forever_line(item.entry),
        ),
    }


def today() -> date:
    """The local calendar date: pack versions are dated by the day the owner released them."""
    return datetime.now(tz=UTC).astimezone().date()


def next_version(pack: str) -> str:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    today_str = today().strftime("%Y.%m.%d")
    last = state.get(pack, {}).get("version", "")
    if last.startswith(today_str):
        parts = last.split(".")
        n = int(parts[3]) + 1 if len(parts) == 4 else 2  # 2026.09.20 -> .2 -> .3 ...
        return f"{today_str}.{n}"
    return today_str


def encoding_tag(release: Release) -> str:
    """Names the audio encoding a pack was built with; a change is a reason to release again."""
    return f"mp3 mono {SAMPLE_RATE} Hz {release.bitrate} no-xing"


def transcode(src: Path, dst: Path, bitrate: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-write_xing",
            "0",
            str(dst),
        ],
        check=True,
    )


def write_manifest(stage: Path, spec: PackSpec, version: str) -> None:
    stage.mkdir(parents=True, exist_ok=True)
    folder = spec.folder
    pack_global = f"{folder}Pack"
    (stage / f"{folder}.toc").write_text(
        f"## Interface: 16001\n## Title: {spec.title}\n## Notes: {spec.notes}\n## Version: {version}\n"
        f"## Author: Quinn Dougherty\n## Dependencies: ForeverVO\n## X-ForeverVO-Pack: 1\n## X-Category: Quests & Leveling\n\n"
        f"Data\\Pack.lua\nData\\Quests.lua\nData\\Gossip.lua\nData\\Books.lua\nData\\NPCs.lua\nData\\Narrator.lua\n"
        f"Data\\Register.lua\n",
        encoding="utf-8",
    )
    data = stage / "Data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "Pack.lua").write_text(
        f'-- Generated by tools/release_pack.py\n{pack_global} = {{\n    name = "{spec.pack_name}",\n'
        f'    version = "{version}",\n    priority = {spec.priority},\n    folder = "{folder}",\n'
        f"    quests = {{}},\n    gossip = {{}},\n    books = {{}},\n    npcs = {{}},\n    narrator = {{}},\n    narratorVoices = {{}},\n}}\n",
        encoding="utf-8",
    )
    (data / "Register.lua").write_text(
        f"if ForeverVO and ForeverVO.RegisterPack then\n    ForeverVO.RegisterPack({pack_global})\nend\n",
        encoding="utf-8",
    )


def stage_tables(pack: str, version: str, config: Config) -> tuple[Path, dict]:
    """Writes the manifest and tables for the pack; returns (stage dir, stats with the file set)."""
    spec = pack_specs(config.release)[pack]
    sources = load_sources()
    catalog = VoiceCatalog(config)
    items = load_items(sources, include_progress=True, catalog=catalog)
    if pack == "delta":
        baseline, book_baseline = load_baseline(), load_baseline((BOOKS_PACK,))
        index = (
            json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
            if baseline is not None or book_baseline is not None
            else {}
        )
        items = [
            item
            for item in items
            if delta_member(item, config, baseline, book_baseline, index)
        ]
    else:
        items = [item for item in items if spec.select(item)]
    stage = RELEASE_DIR / spec.folder
    if stage.exists():
        shutil.rmtree(stage)
    write_manifest(stage, spec, version)
    sound_index = json.loads(SOUND_INDEX.read_text()) if SOUND_INDEX.exists() else {}
    stats = rebuild_tables(
        items,
        sound_index,
        config,
        data_dir=stage / "Data",
        pack_global=f"{spec.folder}Pack",
        sounds_dir=SOUNDS_DIR,
        write_index=False,
    )
    return stage, stats


def package(
    pack: str, version: str, stage: Path, stats: dict, release: Release
) -> Path:
    """Re-encodes the referenced audio into the stage dir and zips it."""
    spec = pack_specs(release)[pack]
    jobs = [
        (
            SOUNDS_DIR / sound_folder(base) / f"{base}.mp3",
            stage / "Sounds" / sound_folder(base) / f"{base}.mp3",
        )
        for base in sorted(stats["files"])
    ]
    # Alternate narrator voices carry their folder in the name (Quests/Narrator/<voice>/<base>),
    # and so does a speaker's other sex (Gossip/Sex/<m|f>/<base>, #304)
    jobs += [
        (SOUNDS_DIR / f"{relative}.mp3", stage / "Sounds" / f"{relative}.mp3")
        for relative in sorted(
            set(stats.get("narratorFiles", ())) | set(stats.get("sexFiles", ()))
        )
    ]
    with ThreadPoolExecutor(max_workers=release.transcode_workers) as pool:
        for n, _ in enumerate(
            pool.map(lambda job: transcode(job[0], job[1], release.bitrate), jobs), 1
        ):
            if n % 1000 == 0 or n == len(jobs):
                print(f"  re-encoded {n}/{len(jobs)}")

    zip_path = RELEASE_DIR / f"{spec.folder}-{version}.zip"
    with zipfile.ZipFile(
        zip_path, "w", zipfile.ZIP_STORED
    ) as zf:  # mp3 does not deflate
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, str(Path(spec.folder) / path.relative_to(stage)))
    size_mb = zip_path.stat().st_size / 1e6
    narrator_files = len(stats.get("narratorFiles", ()))
    sex_files = len(stats.get("sexFiles", ()))
    print(
        f"{zip_path.name}: {stats['quests']} quests, {stats['gossip']} gossip lines, "
        f"{stats.get('books', 0)} book pages, "
        f"{len(stats['files']) + narrator_files + sex_files} files ({narrator_files} alternate "
        f"narrator, {sex_files} in a speaker's other sex), {size_mb:.0f} MB"
    )
    return zip_path


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv() -> None:
    """KEY=VALUE lines from the repo's .env, without overriding the real environment."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def curseforge_config(pack: str, release: Release) -> tuple[str | None, int | None]:
    """(api token, project id) for the pack."""
    load_dotenv()
    key = os.environ.get("CF_API_KEY") or os.environ.get("CURSEFORGE_API_KEY")
    return key, release.curseforge_projects.get(pack)


def game_version_id(key: str) -> int:
    versions = requests.get(
        f"{CF_API}/game/versions", headers={"X-Api-Token": key}, timeout=60
    ).json()
    for entry in versions:
        if entry.get("name") == GAME_VERSION_NAME:
            return int(entry["id"])
    raise SystemExit(f"CurseForge has no game version named {GAME_VERSION_NAME}")


def upload(
    pack: str,
    zip_path: Path,
    version: str,
    stats: dict,
    release_type: str,
    release: Release,
) -> None:
    key, project = curseforge_config(pack, release)
    if not key or not project:
        raise SystemExit(
            f"upload needs CF_API_KEY in .env and a project id for {pack} under "
            f"[release.curseforge_projects] in forever-vo.toml"
        )
    changelog = (
        f"{version}: {stats['quests']} quests, {stats['gossip']} gossip lines, "
        f"{stats.get('books', 0)} book pages, {len(stats['files'])} sound files.\n\n"
        f"Generated from lines captured by players; see https://github.com/quinn-dougherty/forever-vo"
    )
    metadata = {
        "changelog": changelog,
        "changelogType": "markdown",
        "displayName": f"{pack_specs(release)[pack].title} {version}",
        "gameVersions": [game_version_id(key)],
        "releaseType": release_type,
    }
    # Streamed from disk: requests' own multipart encoding builds the whole body
    # in memory, which for the base pack is over a gigabyte.
    with zip_path.open("rb") as f:
        body = MultipartEncoder(
            fields={
                "metadata": json.dumps(metadata),
                "file": (zip_path.name, f, "application/zip"),
            }
        )
        response = requests.post(
            f"{CF_API}/projects/{project}/upload-file",
            headers={"X-Api-Token": key, "Content-Type": body.content_type},
            data=body,
            timeout=3600,
        )
    if response.status_code == 413:
        # Cloudflare in front of the upload API refuses large bodies (887 MB was
        # refused on 2026-09-22; ~30 MB deltas pass). The website accepts up to 2 GB.
        raise SystemExit(
            f"upload refused as too large (HTTP 413) at {zip_path.stat().st_size / 1e6:.0f} MB.\n"
            f"Upload it by hand instead: https://www.curseforge.com/project/{project}/files/upload\n"
            f"  file: {zip_path}\n  game version: {GAME_VERSION_NAME}, type: {release_type}, "
            f"display name: {metadata['displayName']}\n  changelog:\n{changelog}\n"
            f"then add a '{pack}' entry to {STATE_FILE} (version, date, files, zip) as this script would have."
        )
    if response.status_code != 200:
        raise SystemExit(
            f"upload failed: HTTP {response.status_code} {response.text[:300]}"
        )
    print(
        f"uploaded to CurseForge project {project} as file {response.json().get('id')}"
    )


def content_tag(stats: dict, index_path: Path = SOUND_INDEX) -> str:
    """A digest of what the pack's files *say*, not just which files there are.

    The release fingerprint was the list of names, so a release was due only when a
    name appeared or vanished. Regenerating a file changes its audio and never its
    name - a corrected text, a retuned voice, a reference cut from different clips -
    so hours of GPU could land in the working folder and `--if-changed` would decide
    nothing had happened. sound_index already carries the fingerprint generate.py
    computes per file, which covers all three, so this is a hash of that rather than
    of thousands of mp3s.

    The duration joins it for the fourth: a take re-rolled because it came out
    wrong (Kargal Battlescar's 842-accept jumped an octave on "Sergra Darkthorn",
    #333) keeps its text and voice, so only its length says it is a new file.
    """
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.exists()
        else {}
    )

    joined = "\n".join(
        f"{name}={file_stamp(index, name)}" for name in pack_files(stats)
    )
    return hashlib.blake2b(joined.encode("utf-8"), digest_size=8).hexdigest()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pack", choices=PACK_NAMES)
    parser.add_argument(
        "--upload", action="store_true", help="upload to CurseForge after building"
    )
    parser.add_argument(
        "--if-changed",
        action="store_true",
        help="skip when the set of files is unchanged since the last release",
    )
    parser.add_argument(
        "--min-new",
        type=int,
        default=0,
        help="with --if-changed: skip unless at least this many files are new since the last release...",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=0,
        help="...unless the last release is older than this many days and anything changed",
    )
    parser.add_argument(
        "--release-type",
        choices=["alpha", "beta", "release"],
        help="CurseForge file type (default: release)",
    )
    args = parser.parse_args(argv)
    release_type = args.release_type or "release"
    config = load_config()
    release = config.release

    if args.upload:
        key, project = curseforge_config(args.pack, release)
        if not key or not project:
            print(
                f"CurseForge upload not configured for {args.pack}: need CF_API_KEY in .env and a project id under "
                f"[release.curseforge_projects] in forever-vo.toml; skipping"
            )
            return 0

    version = next_version(args.pack)
    stage, stats = stage_tables(args.pack, version, config)

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    fingerprint = pack_files(stats)
    content = content_tag(stats)
    if args.if_changed:
        last = state.get(args.pack, {})
        previous = set(last.get("files", []))
        new_files = len(set(fingerprint) - previous)
        # Either a file appeared or went, or one of them now says something different.
        # A state written before content was recorded has none, and re-releasing once
        # is the right answer there: what it holds cannot be shown to be current.
        changed = last.get("files") != fingerprint or last.get("content") != content
        # A new encoding re-releases every file, so it is due whatever --min-new says.
        reencoded = last.get("encoding") != encoding_tag(release)
        age_days = (
            (today() - date.fromisoformat(last["date"])).days
            if last.get("date")
            else 10**6
        )
        due = reencoded or (
            changed
            and (
                new_files >= args.min_new
                or (args.max_age_days and age_days >= args.max_age_days)
            )
        )
        if not due:
            print(
                f"not due: {new_files} new files since the last release {age_days} days ago "
                f"(need {args.min_new} new or {args.max_age_days} days); nothing to do"
            )
            return 0

    zip_path = package(args.pack, version, stage, stats, release)
    size_mb = zip_path.stat().st_size / 1e6
    if args.upload and args.pack == "delta" and size_mb > release.delta_cap_mb:
        # The upload API refused 574 MB once; well before that the delta has
        # outgrown its job. The Base packs absorb it (their build resets the
        # baseline), and until then players keep the last delta.
        print(
            f"not uploading: the delta is {size_mb:.0f} MB, over delta_cap_mb "
            f"({release.delta_cap_mb}); release Base and Base Endgame, which absorb it"
        )
        return 0

    if args.upload:
        upload(args.pack, zip_path, version, stats, release_type, release)
    state[args.pack] = {
        "version": version,
        "date": today().isoformat(),
        "files": fingerprint,
        "zip": str(zip_path),
        "encoding": encoding_tag(release),
        "content": content,
    }
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    if args.pack in BASE_PACKS or args.pack == BOOKS_PACK:
        record_baseline(args.pack, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
