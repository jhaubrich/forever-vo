"""Builds and (optionally) uploads voice pack releases.

Six packs are released from the one working folder (ForeverVO_Data holds
everything on the maintainer's machine), each line in exactly one of them
(pack_of):

  classic_quests   ForeverVO_Data_Classic_Quests   Classic's quests to level 40
  classic_endgame  ForeverVO_Data_Classic_Endgame  Classic's quests from 41
  classic_gossip   ForeverVO_Data_Classic_Gossip   Classic's gossip
  forever_quests   ForeverVO_Data_Forever_Quests   every quest Classic lacks
  forever_gossip   ForeverVO_Data_Forever_Gossip   every gossip line Classic lacks
  books            ForeverVO_Data_Books            every book, letter and plaque
                                                   page, in every narrator voice

What is Classic's is decided by the VMaNGOS snapshot (tools/data/bulk/classic.json)
alone, a fixed set, so no line ever changes pack. The Classic packs are cut to
stay under what CurseForge's upload API takes (it refused 574 and 887 MB and
took 397), and the Forever packs are Forever's own content, which grows with
play, so every pack uploads through the API whole when it has changed: there is
no overlay of new lines on top (the delta pack, until 2026-10-06) and no hand
upload of a pack near the website's 1 GB cap (Base, until then).

    ./tools/run.sh tools/release_pack.py books                # build zip only
    ./tools/run.sh tools/release_pack.py books --upload       # and upload to CurseForge
    ./tools/run.sh tools/release_pack.py books --upload --if-changed --min-new 10   # nightly use
    ./tools/run.sh tools/release_pack.py classic_quests classic_gossip --upload   # several in one run

With --upload every pack tries the API first. One the API refuses prints what
the website needs and is recorded all the same, as every build is: upload what
you build, soon.

Audio is re-encoded for release (mono 32 kbps mp3 at 22.05 kHz with no
Xing/Info header frame, brought to -16 LUFS since 2026-10-06 (LOUDNORM), about
14 MB per hour of speech; it was 48 kbps until
2026-09-22, when the base pack came to 1.3 GB with a third of the lines still
to go, and carried the header until 2026-09-25, when the client turned out to
misread it and stop every line at 32/56 of its length, see the comment at
SAMPLE_RATE) into tools/data/release/. The zip stores the files uncompressed,
since mp3 does not deflate. Versions are date based (2026.09.21, then
2026.09.21.2 on the same day). Packs upload as "release" files unless
--release-type says otherwise (the CurseForge app hides beta files unless the
user opts in). --if-changed compares each file's stamp (text, voice, length)
and the encoding with the last release recorded in release_state.json; a new
encoding releases every file again. A build re-encodes only the files whose
stamp changed since that release and keeps the rest from the staging folder.

The API key comes from the repo's .env (gitignored): CF_API_KEY=... (the name
the BigWigs packager uses too; CURSEFORGE_API_KEY is still accepted).
Project IDs, the level the base set splits at, the bitrate and the transcode
parallelism are [release] in forever-vo.toml.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import requests
from requests_toolbelt import MultipartEncoder

from tools.classicdb import OUTPUT as CLASSIC_JSON
from tools.config import DATA_DIR, SOUND_INDEX, SOUNDS_DIR, Config, Release, load_config
from tools.generate import (
    Item,
    VoiceCatalog,
    load_items,
    load_sources,
    rebuild_tables,
    sound_folder,
)

RELEASE_DIR = DATA_DIR / "release"
STATE_FILE = DATA_DIR / "release_state.json"
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

# Every release file is brought to one loudness (#513). Chatterbox's takes came
# out anywhere from -34 to -18 LUFS (median -22.6 over 60 files, 2026-10-06), so
# lines jumped in volume from one to the next and many were quiet under the
# game's own sound. Two passes: the first measures, the second applies one gain
# to the whole file (linear=true), so a line's own rise and fall is kept. The
# working files under ForeverVO_Data/Sounds stay as the generator wrote them.
LOUDNORM = "I=-16:TP=-1.5:LRA=11"


def file_stamp(index: dict, name: str) -> str:
    """What a pack file says, from sound_index: text fingerprint, voice, duration
    (the duration gives away a re-rolled take, #333). `name` is a base name, or a
    path under Sounds/ for an alternate (Quests/Narrator/<voice>/<base>,
    Gossip/Sex/<m|f>/<base>, Quests/Speaker/<speaker>/<base>), whose index key
    drops the Quests/Gossip folder."""
    if "/" in name:
        name = name.split("/", 1)[1]
    entry = index.get(name)
    if not isinstance(entry, dict):
        return "?"
    return f"{entry.get('t') or '?'}:{entry.get('v') or '?'}:{entry.get('d') or '?'}"


def pack_files(stats: dict) -> list[str]:
    return (
        sorted(stats["files"])
        + sorted(stats.get("narratorFiles", ()))
        + sorted(stats.get("sexFiles", ()))
        + sorted(stats.get("speakerFiles", ()))
    )


def file_stamps(stats: dict, index_path: Path = SOUND_INDEX) -> dict[str, str]:
    """What every file of a built pack says, by name: what the next build of the
    pack is compared with (changed_files) and which re-encoded files it can keep."""
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.exists()
        else {}
    )
    return {name: file_stamp(index, name) for name in pack_files(stats)}


def changed_files(previous: dict[str, str] | None, stamps: dict[str, str]) -> int:
    """How many files a build adds, drops or changes against the last release: a
    re-voiced or re-rolled line keeps its name and changes its stamp, so it counts
    as much as a new one. A release recorded before stamps were has none, and every
    file counts: what it held cannot be shown to be current."""
    if previous is None:
        return len(stamps) or 1
    gone = set(previous) - set(stamps)
    return len(gone) + sum(
        previous.get(name) != stamp for name, stamp in stamps.items()
    )


@dataclass(frozen=True)
class Classic:
    """What the VMaNGOS snapshot holds, which is all that decides a line's pack:
    the level of every Classic quest and the key of every Classic gossip line. The
    snapshot is a fixed set, so no line ever moves to another pack."""

    quest_levels: dict[int, int]
    gossip: frozenset[str]


def load_classic(path: Path = CLASSIC_JSON) -> Classic:
    if not path.exists():
        # Without it every line would read as Forever's and land in the Forever packs
        raise SystemExit(f"{path} is missing: run classicdb.py before release_pack.py")
    data = json.loads(path.read_text(encoding="utf-8"))
    levels: dict[int, int] = {}
    for entry in data.get("quests", {}).values():
        quest = int(entry["questID"])
        levels[quest] = max(levels.get(quest, 0), int(entry.get("level") or 0))
    return Classic(levels, frozenset(data.get("gossip", {})))


def pack_of(item: Item, classic: Classic, split_level: int) -> str:
    """The one pack a line ships in. Classic's lines (by quest ID, or gossip key)
    fill three packs cut to fit CurseForge's upload API; whatever Classic does not
    have is Forever's own, and grows with play: its quests and its gossip. A quest's
    alternates sit in the pack with the quest, since the addon looks them up in the
    pack that had the entry. A Classic quest Forever rewords stays Classic's, under
    its quest ID; a Classic NPC's new gossip has a key Classic lacks and is Forever's.
    """
    if item.kind == "books":
        return "books"
    if item.kind == "quests":
        level = classic.quest_levels.get(int(item.entry["questID"]))
        if level is None:
            return "forever_quests"
        return "classic_endgame" if level > split_level else "classic_quests"
    return "classic_gossip" if item.key in classic.gossip else "forever_gossip"


@dataclass(frozen=True)
class PackSpec:
    folder: str  # the addon folder the pack installs as; its global is <folder>Pack
    title: str
    pack_name: str  # what the addon shows as the pack's name
    notes: str


PACK_NAMES = (
    "classic_quests",
    "classic_endgame",
    "classic_gossip",
    "forever_quests",
    "forever_gossip",
    "books",
)
# Every pack sits at one priority: no two hold the same line
PRIORITY = 100


def pack_specs(release: Release) -> dict[str, PackSpec]:
    split = release.base_split_level
    return {
        "classic_quests": PackSpec(
            folder="ForeverVO_Data_Classic_Quests",
            title="Forever Voiceover: Classic Quests",
            pack_name="Classic Quests",
            notes=f"Classic's quests to level {split}, voiced.",
        ),
        "classic_endgame": PackSpec(
            folder="ForeverVO_Data_Classic_Endgame",
            title="Forever Voiceover: Classic Endgame",
            pack_name="Classic Endgame",
            notes=f"Classic's quests from level {split + 1}, voiced.",
        ),
        "classic_gossip": PackSpec(
            folder="ForeverVO_Data_Classic_Gossip",
            title="Forever Voiceover: Classic Gossip",
            pack_name="Classic Gossip",
            notes="What Classic's NPCs say when you talk to them, voiced.",
        ),
        "forever_quests": PackSpec(
            folder="ForeverVO_Data_Forever_Quests",
            title="Forever Voiceover: Forever Quests",
            pack_name="Forever Quests",
            notes="The quests Forever adds, voiced from lines players capture. Updated as they come in.",
        ),
        "forever_gossip": PackSpec(
            folder="ForeverVO_Data_Forever_Gossip",
            title="Forever Voiceover: Forever Gossip",
            pack_name="Forever Gossip",
            notes="What Forever's NPCs say, voiced from lines players capture. Updated as they come in.",
        ),
        "books": PackSpec(
            folder="ForeverVO_Data_Books",
            title="Forever Voiceover: Books",
            pack_name="Books",
            notes="Books, letters and plaques, read by the narrator.",
        ),
    }


def record_release(pack: str, state: dict) -> None:
    """Writes a built pack's state: what --if-changed compares the next build with."""
    states = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    states[pack] = state
    STATE_FILE.write_text(json.dumps(states, indent=1), encoding="utf-8")


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
    return f"mp3 mono {SAMPLE_RATE} Hz {release.bitrate} no-xing loudnorm {LOUDNORM}"


def loudnorm_filter(src: Path) -> list[str]:
    """The second pass's -af for `src`, from a first pass that measures it; none
    for a file with nothing to measure (silence reads -inf), which is left as it is."""
    run = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(src),
            "-af",
            f"loudnorm={LOUDNORM}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    measured = json.loads(
        run.stderr[run.stderr.rindex("{") : run.stderr.rindex("}") + 1]
    )
    values = [
        measured[key]
        for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    ]
    if any(not math.isfinite(float(value)) for value in values):
        return []
    i, tp, lra, thresh, offset = values
    second = (
        f"loudnorm={LOUDNORM}:measured_I={i}:measured_TP={tp}:measured_LRA={lra}"
        f":measured_thresh={thresh}:offset={offset}:linear=true"
    )
    return ["-af", second]


def transcode(src: Path, dst: Path, bitrate: str) -> None:
    """Written to a temporary file and renamed, so a build cut short never leaves a
    half-written file for the next one to keep."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}.part.mp3")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            *loudnorm_filter(src),
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
            str(tmp),
        ],
        check=True,
    )
    os.replace(tmp, dst)


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
        f'    version = "{version}",\n    priority = {PRIORITY},\n    folder = "{folder}",\n'
        f"    quests = {{}},\n    gossip = {{}},\n    books = {{}},\n    npcs = {{}},\n    narrator = {{}},\n    narratorVoices = {{}},\n}}\n",
        encoding="utf-8",
    )
    (data / "Register.lua").write_text(
        f"if ForeverVO and ForeverVO.RegisterPack then\n    ForeverVO.RegisterPack({pack_global})\nend\n",
        encoding="utf-8",
    )


def stage_tables(pack: str, version: str, config: Config) -> tuple[Path, dict]:
    """Writes the manifest and tables for the pack; returns (stage dir, stats with the
    file set). The stage's Sounds stay: package keeps what has not changed."""
    spec = pack_specs(config.release)[pack]
    classic = load_classic()
    split = config.release.base_split_level
    sources = load_sources()
    catalog = VoiceCatalog(config)
    items = [
        item
        for item in load_items(sources, include_progress=True, catalog=catalog)
        if pack_of(item, classic, split) == pack
    ]
    stage = RELEASE_DIR / spec.folder
    for old in (stage / "Data", stage / f"{spec.folder}.toc"):
        if old.is_dir():
            shutil.rmtree(old)
        elif old.exists():
            old.unlink()
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


def sound_path(name: str) -> Path:
    """A pack file's path under Sounds/. Alternate narrator voices carry their folder
    in the name (Quests/Narrator/<voice>/<base>), and so do a speaker's other sex
    (Gossip/Sex/<m|f>/<base>, #304) and a quest line's other speakers
    (Quests/Speaker/<speaker>/<base>, #948)."""
    return (
        Path(f"{name}.mp3") if "/" in name else Path(sound_folder(name), f"{name}.mp3")
    )


def package(
    pack: str,
    version: str,
    stage: Path,
    stats: dict,
    release: Release,
    keep: dict[str, str] | None = None,
) -> Path:
    """Re-encodes the referenced audio into the stage dir and zips it. `keep` is the
    stamps of the last release in this encoding: a file whose stamp is the same is
    already in the stage as it should be, and is not encoded again (a whole Classic
    pack takes about 20 minutes; a night's changes, seconds). Whatever the pack no
    longer has leaves the stage."""
    spec = pack_specs(release)[pack]
    sounds = stage / "Sounds"
    names = pack_files(stats)
    stamps = file_stamps(stats)
    wanted = {sounds / sound_path(name) for name in names}
    if sounds.exists():
        for path in sounds.rglob("*.mp3"):
            if path not in wanted:
                path.unlink()
    jobs = [
        (SOUNDS_DIR / sound_path(name), sounds / sound_path(name))
        for name in names
        if not keep
        or keep.get(name) != stamps[name]
        or not (sounds / sound_path(name)).exists()
    ]
    print(f"  re-encoding {len(jobs)} of {len(names)} files")
    with ThreadPoolExecutor(max_workers=release.transcode_workers) as pool:
        for n, _ in enumerate(
            pool.map(lambda job: transcode(job[0], job[1], release.bitrate), jobs), 1
        ):
            if n % 1000 == 0:
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
    speaker_files = len(stats.get("speakerFiles", ()))
    print(
        f"{zip_path.name}: {stats['quests']} quests, {stats['gossip']} gossip lines, "
        f"{stats.get('books', 0)} book pages, "
        f"{len(stats['files']) + narrator_files + sex_files + speaker_files} files "
        f"({narrator_files} alternate narrator, {sex_files} in a speaker's other sex, "
        f"{speaker_files} for a quest's other speakers), {size_mb:.0f} MB"
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


class UploadFailed(Exception):
    """The API did not take the file; the pack waits on a hand upload."""


def changelog_for(version: str, stats: dict) -> str:
    return (
        f"{version}: {stats['quests']} quests, {stats['gossip']} gossip lines, "
        f"{stats.get('books', 0)} book pages, {len(stats['files'])} sound files.\n\n"
        f"Generated from lines captured by players; see https://github.com/quinn-dougherty/forever-vo"
    )


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
    metadata = {
        "changelog": changelog_for(version, stats),
        "changelogType": "markdown",
        "displayName": f"{pack_specs(release)[pack].title} {version}",
        "gameVersions": [game_version_id(key)],
        "releaseType": release_type,
    }
    # Streamed from disk: requests' own multipart encoding builds the whole body
    # in memory, which for the base pack is over a gigabyte.
    try:
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
    except requests.RequestException as error:
        raise UploadFailed(f"upload failed: {error}") from error
    if response.status_code == 413:
        # Cloudflare in front of the upload API refuses large bodies (887 MB was
        # refused on 2026-09-22; ~30 MB deltas pass). The website accepts up to 1 GB.
        raise UploadFailed(
            f"upload refused as too large (HTTP 413) at {zip_path.stat().st_size / 1e6:.0f} MB"
        )
    if response.status_code != 200:
        raise UploadFailed(
            f"upload failed: HTTP {response.status_code} {response.text[:300]}"
        )
    print(
        f"uploaded to CurseForge project {project} as file {response.json().get('id')}"
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("packs", nargs="+", choices=PACK_NAMES, metavar="pack")
    parser.add_argument(
        "--upload", action="store_true", help="upload to CurseForge after building"
    )
    parser.add_argument(
        "--if-changed",
        action="store_true",
        help="skip when no file is new, gone or changed since the last release",
    )
    parser.add_argument(
        "--min-new",
        type=int,
        default=0,
        help="with --if-changed: skip unless at least this many files are new, gone or changed since the last release...",
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
    config = load_config()
    refused = [pack for pack in args.packs if release_one(pack, args, config)]
    if refused:
        # Last, so the nightly log's tail shows it and daily.sh can find it
        print(f"upload by hand: {' '.join(refused)} (details above)")
        return 1
    return 0


def release_one(pack: str, args: argparse.Namespace, config: Config) -> bool:
    """Builds one pack and, with --upload, uploads it. True when the upload
    failed and the pack now waits on a hand upload."""
    release = config.release
    release_type = args.release_type or "release"
    if args.upload:
        key, project = curseforge_config(pack, release)
        if not key or not project:
            print(
                f"CurseForge upload not configured for {pack}: need CF_API_KEY in .env and a project id under "
                f"[release.curseforge_projects] in forever-vo.toml; skipping"
            )
            return False

    version = next_version(pack)
    stage, stats = stage_tables(pack, version, config)

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    last = state.get(pack, {})
    fingerprint = pack_files(stats)
    if not fingerprint:
        # A pack before any of its lines is voiced: an empty one would only confuse players
        print(f"{pack}: no sound files yet; nothing to release")
        return False
    stamps = file_stamps(stats)
    same_encoding = last.get("encoding") == encoding_tag(release)
    if args.if_changed:
        changed = changed_files(last.get("stamps"), stamps)
        age_days = (
            (today() - date.fromisoformat(last["date"])).days
            if last.get("date")
            else 10**6
        )
        # A new encoding re-releases every file, so it is due whatever --min-new says
        due = not same_encoding or (
            changed > 0
            and (
                changed >= args.min_new
                or (args.max_age_days and age_days >= args.max_age_days)
            )
        )
        if not due:
            print(
                f"{pack} not due: {changed} files new, gone or changed since the last release "
                f"{age_days} days ago (need {args.min_new} or {args.max_age_days} days); nothing to do"
            )
            return False

    zip_path = package(
        pack,
        version,
        stage,
        stats,
        release,
        keep=last.get("stamps") if same_encoding else None,
    )
    record = {
        "version": version,
        "date": today().isoformat(),
        "files": fingerprint,
        "zip": str(zip_path),
        "encoding": encoding_tag(release),
        "stamps": stamps,
    }
    refused = False
    if args.upload:
        try:
            upload(pack, zip_path, version, stats, release_type, release)
        except UploadFailed as error:
            refused = True
            _, project = curseforge_config(pack, release)
            print(
                f"{pack}: {error}.\n"
                f"Upload it by hand: https://www.curseforge.com/project/{project}/files/upload\n"
                f"  file: {zip_path}\n  game version: {GAME_VERSION_NAME}, type: {release_type}, "
                f"display name: {pack_specs(release)[pack].title} {version}\n"
                f"  changelog:\n{changelog_for(version, stats)}"
            )
    # Recorded whether or not the API took it: a refused pack is uploaded by hand
    # straight after, and its stage holds what was built
    record_release(pack, record)
    return refused


if __name__ == "__main__":
    sys.exit(main())
