"""Builds and (optionally) uploads voice pack releases.

Three packs are released from the one working folder (ForeverVO_Data holds
everything on the maintainer's machine):

  base          ForeverVO_Data_Base          Classic-era lines (source: classic):
  base_endgame  ForeverVO_Data_Base_Endgame  quests to level 40 with all gossip,
                                             and quests from 41, split so each
                                             fits CurseForge's 1 GB website cap.
                                             Huge, rarely released, uploaded by
                                             hand. (The maintainer's working
                                             folder stays ForeverVO_Data; they
                                             coexist because their pack names
                                             differ from it.)
  delta         ForeverVO_Data_Forever       lines captured in game, from the
                                             beta cache or from the community.
                                             Small, released often, higher
                                             priority so it overrides the base.

    ./tools/run.sh tools/release_pack.py delta               # build zip only
    ./tools/run.sh tools/release_pack.py delta --upload      # and upload to CurseForge
    ./tools/run.sh tools/release_pack.py delta --upload --if-changed   # nightly use
    ./tools/run.sh tools/release_pack.py base && ./tools/run.sh tools/release_pack.py base_endgame

Audio is re-encoded for release (mono 32 kbps mp3 at 22.05 kHz, about 14 MB per
hour of speech; it was 48 kbps until 2026-09-22, when the base pack came to
1.3 GB with a third of the lines still to go) into tools/data/release/. The
zip stores the files uncompressed, since mp3 does not deflate. Versions are
date based (2026.09.21, then 2026.09.21.2 on the same day). The base pack
upload as "release" files unless --release-type says otherwise (the delta was
"beta" until 2026-09-24: the CurseForge app hides beta files unless the user
opts in, so default installs never got it).

The API key comes from the repo's .env (gitignored): CF_API_KEY=... (the name
the BigWigs packager uses too; CURSEFORGE_API_KEY is still accepted).
Project IDs are in config.CURSEFORGE_PROJECTS.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests
from requests_toolbelt import MultipartEncoder

from tools.config import CURSEFORGE_PROJECTS, DATA_DIR, SOUND_INDEX, SOUNDS_DIR
from tools.generate import load_items, load_sources, rebuild_tables, sound_folder

RELEASE_DIR = DATA_DIR / "release"
STATE_FILE = DATA_DIR / "release_state.json"
CF_API = "https://wow.curseforge.com/api"
GAME_VERSION_NAME = "1.60.1"

CLASSIC_JSON = DATA_DIR / "bulk" / "classic.json"


def classic_quest_ids() -> set[int]:
    if not CLASSIC_JSON.exists():
        return set()
    data = json.loads(CLASSIC_JSON.read_text(encoding="utf-8"))
    return {entry["questID"] for entry in data["quests"].values()}


def is_forever_line(entry: dict, classic_ids: set[int]) -> bool:
    """Delta pack membership: lines players saw in game, or quests Classic never had.
    Beta-cache text for a quest that exists in Classic stays in the base pack, so the
    delta does not grow as the bulk run works through Classic."""
    source = entry.get("source", "classic")
    if entry.get("player") or source in ("capture", "community"):
        return True
    if source == "questcache":
        return entry.get("questID") not in classic_ids
    return False


# The CurseForge website takes files up to 1 GB (the API less, see CLAUDE.md),
# and the Classic set with its alternate narrators is 1.36 GB, so it ships as
# two projects. A quest's alternates must sit in the same pack as the quest
# (the addon looks them up in the pack that had the entry), so the cut is by
# quest level: 1-40 with all gossip on one side (800 MB), 41+ ("endgame") on the other
# (570 MB), each with about 200 MB of headroom for another narrator voice.
# Cutting at 50 instead would put the first side back over the cap.
BASE_SPLIT_LEVEL = 40


def base_part(entry: dict) -> int:
    if entry.get("questID") and int(entry.get("level") or 0) > BASE_SPLIT_LEVEL:
        return 2
    return 1


PACKS = {
    "base": {
        "folder": "ForeverVO_Data_Base",
        "title": "Forever Voiceover Data: Base",
        "pack_name": "Classic",
        "priority": 100,
        "notes": f"Classic-era quests to level {BASE_SPLIT_LEVEL} and all gossip, voiced. Install with Forever Voiceover and Base Endgame.",
        "select": lambda entry, classic_ids: not is_forever_line(entry, classic_ids) and base_part(entry) == 1,
    },
    "base_endgame": {
        "folder": "ForeverVO_Data_Base_Endgame",
        "title": "Forever Voiceover Data: Base Endgame",
        "pack_name": "Classic Endgame",
        "priority": 100,
        "notes": f"Classic-era quests from level {BASE_SPLIT_LEVEL + 1}, voiced. Install with Forever Voiceover and Base.",
        "select": lambda entry, classic_ids: not is_forever_line(entry, classic_ids) and base_part(entry) == 2,
    },
    "delta": {
        "folder": "ForeverVO_Data_Forever",
        "title": "Forever Voiceover Data: Forever",
        "pack_name": "Forever",
        "priority": 200,
        "notes": "New and revised Forever lines from player captures. Sits on top of Forever Voiceover Data.",
        "select": lambda entry, classic_ids: is_forever_line(entry, classic_ids),
    },
}


def next_version(pack: str) -> str:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    today = date.today().strftime("%Y.%m.%d")
    last = state.get(pack, {}).get("version", "")
    if last.startswith(today):
        parts = last.split(".")
        n = int(parts[3]) + 1 if len(parts) == 4 else 2   # 2026.09.20 -> .2 -> .3 ...
        return f"{today}.{n}"
    return today


RELEASE_BITRATE = "32k"
TRANSCODE_WORKERS = 4   # ffmpeg is CPU work; leave cores for the GPU workers' own decoding


def transcode(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "22050",
         "-codec:a", "libmp3lame", "-b:a", RELEASE_BITRATE, str(dst)],
        check=True,
    )


def write_manifest(stage: Path, spec: dict, version: str) -> None:
    stage.mkdir(parents=True, exist_ok=True)
    folder = spec["folder"]
    pack_global = f"{folder}Pack"
    (stage / f"{folder}.toc").write_text(
        f"## Interface: 16001\n## Title: {spec['title']}\n## Notes: {spec['notes']}\n## Version: {version}\n"
        f"## Author: Quinn Dougherty\n## Dependencies: ForeverVO\n## X-ForeverVO-Pack: 1\n## X-Category: Quests & Leveling\n\n"
        f"Data\\Pack.lua\nData\\Quests.lua\nData\\Gossip.lua\nData\\NPCs.lua\nData\\Narrator.lua\nData\\Register.lua\n",
        encoding="utf-8",
    )
    data = stage / "Data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "Pack.lua").write_text(
        f"-- Generated by tools/release_pack.py\n{pack_global} = {{\n    name = \"{spec['pack_name']}\",\n"
        f"    version = \"{version}\",\n    priority = {spec['priority']},\n    folder = \"{folder}\",\n"
        f"    quests = {{}},\n    gossip = {{}},\n    npcs = {{}},\n    narrator = {{}},\n    narratorVoices = {{}},\n}}\n",
        encoding="utf-8",
    )
    (data / "Register.lua").write_text(
        f"if ForeverVO and ForeverVO.RegisterPack then\n    ForeverVO.RegisterPack({pack_global})\nend\n",
        encoding="utf-8",
    )


def stage_tables(pack: str, version: str) -> tuple[Path, dict]:
    """Writes the manifest and tables for the pack; returns (stage dir, stats with the file set)."""
    spec = PACKS[pack]
    sources = load_sources()
    classic_ids = classic_quest_ids()
    items = [item for item in load_items(sources, include_progress=True) if spec["select"](item.entry, classic_ids)]
    stage = RELEASE_DIR / spec["folder"]
    if stage.exists():
        shutil.rmtree(stage)
    write_manifest(stage, spec, version)
    sound_index = json.loads(SOUND_INDEX.read_text()) if SOUND_INDEX.exists() else {}
    stats = rebuild_tables(items, sound_index, data_dir=stage / "Data", pack_global=f"{spec['folder']}Pack",
                           sounds_dir=SOUNDS_DIR, write_index=False)
    return stage, stats


def package(pack: str, version: str, stage: Path, stats: dict) -> Path:
    """Re-encodes the referenced audio into the stage dir and zips it."""
    spec = PACKS[pack]
    jobs = [(SOUNDS_DIR / sound_folder(base) / f"{base}.mp3", stage / "Sounds" / sound_folder(base) / f"{base}.mp3")
            for base in sorted(stats["files"])]
    # Alternate narrator voices carry their folder in the name (Quests/Narrator/<voice>/<base>)
    jobs += [(SOUNDS_DIR / f"{relative}.mp3", stage / "Sounds" / f"{relative}.mp3")
             for relative in sorted(stats.get("narratorFiles", ()))]
    with ThreadPoolExecutor(max_workers=TRANSCODE_WORKERS) as pool:
        for n, _ in enumerate(pool.map(lambda job: transcode(*job), jobs), 1):
            if n % 1000 == 0 or n == len(jobs):
                print(f"  re-encoded {n}/{len(jobs)}")

    zip_path = RELEASE_DIR / f"{spec['folder']}-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:   # mp3 does not deflate
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, str(Path(spec["folder"]) / path.relative_to(stage)))
    size_mb = zip_path.stat().st_size / 1e6
    narrator_files = len(stats.get("narratorFiles", ()))
    print(f"{zip_path.name}: {stats['quests']} quests, {stats['gossip']} gossip lines, "
          f"{len(stats['files']) + narrator_files} files ({narrator_files} alternate narrator), {size_mb:.0f} MB")
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


def curseforge_config(pack: str) -> tuple[str | None, int | None]:
    """(api token, project id) for the pack."""
    load_dotenv()
    key = os.environ.get("CF_API_KEY") or os.environ.get("CURSEFORGE_API_KEY")
    return key, CURSEFORGE_PROJECTS.get(pack)


def game_version_id(key: str) -> int:
    versions = requests.get(f"{CF_API}/game/versions", headers={"X-Api-Token": key}, timeout=60).json()
    for entry in versions:
        if entry.get("name") == GAME_VERSION_NAME:
            return int(entry["id"])
    raise SystemExit(f"CurseForge has no game version named {GAME_VERSION_NAME}")


def upload(pack: str, zip_path: Path, version: str, stats: dict, release_type: str) -> None:
    key, project = curseforge_config(pack)
    if not key or not project:
        raise SystemExit(f"upload needs CF_API_KEY in .env and a project id for {pack} in config.CURSEFORGE_PROJECTS")
    changelog = (f"{version}: {stats['quests']} quests, {stats['gossip']} gossip lines, {len(stats['files'])} sound files.\n\n"
                 f"Generated from lines captured by players; see https://github.com/quinn-dougherty/forever-vo")
    metadata = {
        "changelog": changelog,
        "changelogType": "markdown",
        "displayName": f"{PACKS[pack]['title']} {version}",
        "gameVersions": [game_version_id(key)],
        "releaseType": release_type,
    }
    # Streamed from disk: requests' own multipart encoding builds the whole body
    # in memory, which for the base pack is over a gigabyte.
    with zip_path.open("rb") as f:
        body = MultipartEncoder(fields={
            "metadata": json.dumps(metadata),
            "file": (zip_path.name, f, "application/zip"),
        })
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
        raise SystemExit(f"upload failed: HTTP {response.status_code} {response.text[:300]}")
    print(f"uploaded to CurseForge project {project} as file {response.json().get('id')}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pack", choices=sorted(PACKS))
    parser.add_argument("--upload", action="store_true", help="upload to CurseForge after building")
    parser.add_argument("--if-changed", action="store_true", help="skip when the set of files is unchanged since the last release")
    parser.add_argument("--min-new", type=int, default=0, help="with --if-changed: skip unless at least this many files are new since the last release...")
    parser.add_argument("--max-age-days", type=int, default=0, help="...unless the last release is older than this many days and anything changed")
    parser.add_argument("--release-type", choices=["alpha", "beta", "release"],
                        help="CurseForge file type (default: release)")
    args = parser.parse_args(argv)
    release_type = args.release_type or "release"

    if args.upload:
        key, project = curseforge_config(args.pack)
        if not key or not project:
            print(f"CurseForge upload not configured for {args.pack}: need CF_API_KEY in .env and a project id in config.CURSEFORGE_PROJECTS; skipping")
            return 0

    version = next_version(args.pack)
    stage, stats = stage_tables(args.pack, version)

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    fingerprint = sorted(stats["files"]) + sorted(stats.get("narratorFiles", ()))
    if args.if_changed:
        last = state.get(args.pack, {})
        previous = set(last.get("files", []))
        new_files = len(set(fingerprint) - previous)
        changed = last.get("files") != fingerprint
        age_days = (date.today() - date.fromisoformat(last["date"])).days if last.get("date") else 10**6
        due = changed and (new_files >= args.min_new or (args.max_age_days and age_days >= args.max_age_days))
        if not due:
            print(f"not due: {new_files} new files since the last release {age_days} days ago "
                  f"(need {args.min_new} new or {args.max_age_days} days); nothing to do")
            return 0

    zip_path = package(args.pack, version, stage, stats)

    if args.upload:
        upload(args.pack, zip_path, version, stats, release_type)
    state[args.pack] = {"version": version, "date": date.today().isoformat(), "files": fingerprint, "zip": str(zip_path)}
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
