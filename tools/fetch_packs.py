"""Download the CurseForge voice packs into a directory audition can play from.

Optional. With nothing set, the page still reads the game client's AddOns
folder from WOW_DIR, and this script is never run. A checkout with no client
can point AUDITION_ADDONS at a directory of ForeverVO_Data* folders. The
default download lands in ./addons, which is gitignored, and holds the
released packs (every one with a project ID in forever-vo.toml; the folders
are release_pack's). The client is not touched
unless AUDITION_ADDONS or --addons names that folder.

    ./tools/run.sh fvo-fetch-packs
    AUDITION_ADDONS="$PWD/addons" ./tools/run.sh audition

The public file list needs no API key. The bytes come from ForgeCDN at the
path CurseForge derives from the file id (id // 1000, id % 1000, file name).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

import requests

from tools.config import ROOT, load_config

ADDONS_DIR = ROOT / "addons"
STAMP = ".fetched.json"
# CurseForge releaseType: 1 release, 2 beta, 3 alpha. The packs ship as release.
RELEASE = 1
LIST_URL = "https://www.curseforge.com/api/v1/mods/{project}/files"
CDN = "https://edge.forgecdn.net/files/{hi}/{lo}/{name}"
# pack key in forever-vo.toml -> the folder the zip installs as
FOLDERS = {
    "classic_quests": "ForeverVO_Data_Classic_Quests",
    "classic_endgame": "ForeverVO_Data_Classic_Endgame",
    "classic_gossip": "ForeverVO_Data_Classic_Gossip",
    "forever_quests": "ForeverVO_Data_Forever_Quests",
    "forever_gossip": "ForeverVO_Data_Forever_Gossip",
    "books": "ForeverVO_Data_Books",
}
# The layout before 2026-10-06, whose folders a download made then still holds
RETIRED = (
    "ForeverVO_Data_Base",
    "ForeverVO_Data_Base_Endgame",
    "ForeverVO_Data_Forever",
)


def choose_file(files: list[dict]) -> dict:
    """The newest release. The list is usually newest-first already; the date
    decides, so a resorted page cannot install an older file."""
    releases = [item for item in files if item.get("releaseType") == RELEASE]
    pool = releases or list(files)
    if not pool:
        raise SystemExit("CurseForge returned no files")
    pool.sort(key=lambda item: item.get("dateCreated") or "", reverse=True)
    return pool[0]


def forgecdn_url(file_id: int, file_name: str) -> str:
    """The CDN path CurseForge uses. file id 8999248 is files/8999/248/<name>."""
    return CDN.format(hi=file_id // 1000, lo=file_id % 1000, name=quote(file_name))


def list_files(project: int) -> list[dict]:
    response = requests.get(
        LIST_URL.format(project=project),
        params={"pageSize": 20, "sortField": 1, "sortOrder": "desc"},
        headers={"Accept": "application/json", "User-Agent": "forever-vo-fetch-packs"},
        timeout=60,
    )
    response.raise_for_status()
    files = response.json().get("data") or []
    if not files:
        raise SystemExit(f"CurseForge project {project} has no files")
    return files


def download(url: str, dest: Path, expected: int | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    written = 0
    with requests.get(url, stream=True, timeout=(30, 120)) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
                if written % (50 * 1024 * 1024) < len(chunk):
                    print(f"  {dest.name}: {written / 1e6:.0f} MB", flush=True)
    if expected is not None and written != expected:
        partial.unlink(missing_ok=True)
        raise SystemExit(
            f"{dest.name}: got {written} bytes, CurseForge said {expected}"
        )
    partial.replace(dest)


def install_zip(zip_path: Path, addons: Path, folder: str) -> Path:
    """Replace addons/<folder> with the zip's copy of it. A bad path, or a zip
    that is not that folder, leaves the previous directory in place."""
    prefix = folder + "/"
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            parts = Path(name).parts
            if not parts or parts[0] != folder or ".." in parts:
                raise SystemExit(f"{zip_path.name} has an unexpected member {name!r}")
        incoming = addons / f".{folder}.incoming"
        if incoming.exists():
            shutil.rmtree(incoming)
        incoming.mkdir(parents=True)
        try:
            archive.extractall(incoming)
        except Exception:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
    extracted = incoming / folder
    if (
        not (extracted / "Sounds").is_dir()
        or not (extracted / "Data" / "Pack.lua").is_file()
    ):
        shutil.rmtree(incoming, ignore_errors=True)
        raise SystemExit(f"{zip_path.name} has no {prefix}Sounds or Data/Pack.lua")
    target = addons / folder
    backup = addons / f".{folder}.old"
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        target.rename(backup)
    extracted.rename(target)
    shutil.rmtree(incoming, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup)
    return target


def stamp_path(addons: Path) -> Path:
    return addons / STAMP


def read_stamp(addons: Path) -> dict:
    path = stamp_path(addons)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_stamp(addons: Path, stamp: dict) -> None:
    stamp_path(addons).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")


def fetch_pack(pack: str, project: int, addons: Path, force: bool, stamp: dict) -> None:
    folder = FOLDERS[pack]
    chosen = choose_file(list_files(project))
    file_id = int(chosen["id"])
    file_name = str(chosen["fileName"])
    previous = stamp.get(folder) or {}
    if (
        not force
        and previous.get("id") == file_id
        and (addons / folder / "Sounds").is_dir()
    ):
        print(f"{folder}: already {file_name}")
        return
    url = forgecdn_url(file_id, file_name)
    print(f"{folder}: {file_name} ({int(chosen.get('fileLength') or 0) / 1e6:.0f} MB)")
    print(f"  {url}")
    addons.mkdir(parents=True, exist_ok=True)
    zip_path = addons / ".partial" / file_name
    download(
        url, zip_path, int(chosen["fileLength"]) if chosen.get("fileLength") else None
    )
    try:
        install_zip(zip_path, addons, folder)
    finally:
        zip_path.unlink(missing_ok=True)
    stamp[folder] = {
        "id": file_id,
        "fileName": file_name,
        "fileLength": chosen.get("fileLength"),
        "project": project,
    }
    write_stamp(addons, stamp)
    print(f"{folder}: installed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--addons",
        type=Path,
        default=Path(os.environ.get("AUDITION_ADDONS", str(ADDONS_DIR))),
        help="directory the packs are unpacked into (default: AUDITION_ADDONS, else ./addons)",
    )
    parser.add_argument(
        "--pack",
        action="append",
        choices=tuple(FOLDERS),
        help="only this pack, repeatable (default: every one with a project ID)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="download again even if the file id matches",
    )
    args = parser.parse_args(argv)
    projects = load_config().release.curseforge_projects
    wanted = args.pack or [pack for pack in FOLDERS if projects.get(pack)]
    addons = args.addons
    stamp = read_stamp(addons)
    for folder in RETIRED:
        if (addons / folder).is_dir():
            print(
                f"{addons / folder}: a retired pack, replaced by the packs above; delete it"
            )
    for pack in wanted:
        project = projects.get(pack)
        if not project:
            raise SystemExit(
                f"no [release.curseforge_projects].{pack} in forever-vo.toml"
            )
        fetch_pack(pack, project, addons, args.force, stamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
