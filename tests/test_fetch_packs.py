"""The CurseForge pack fetch: which file, which URL, and how the zip lands."""

from __future__ import annotations

import zipfile
from pathlib import Path

from tools.audition import ADDONS_DIR, addons_from_env
from tools.fetch_packs import choose_file, forgecdn_url, install_zip


def test_choose_file_prefers_the_newest_release() -> None:
    chosen = choose_file(
        [
            {
                "id": 1,
                "releaseType": 2,
                "dateCreated": "2026-09-29T00:00:00Z",
                "fileName": "beta.zip",
            },
            {
                "id": 2,
                "releaseType": 1,
                "dateCreated": "2026-09-20T00:00:00Z",
                "fileName": "old.zip",
            },
            {
                "id": 3,
                "releaseType": 1,
                "dateCreated": "2026-09-28T00:00:00Z",
                "fileName": "new.zip",
            },
        ]
    )
    assert chosen["id"] == 3


def test_forgecdn_url_splits_the_file_id() -> None:
    assert (
        forgecdn_url(8999248, "ForeverVO_Data_Base-2026.09.28.zip")
        == "https://edge.forgecdn.net/files/8999/248/ForeverVO_Data_Base-2026.09.28.zip"
    )


def test_install_zip_replaces_the_pack_folder(tmp_path: Path) -> None:
    folder = "ForeverVO_Data_Base"
    addons = tmp_path / "addons"
    old = addons / folder / "Sounds" / "Quests"
    old.mkdir(parents=True)
    (old / "gone.mp3").write_bytes(b"old")
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(f"{folder}/Data/Pack.lua", "pack = {}\n")
        archive.writestr(f"{folder}/Sounds/Quests/1-accept.mp3", b"audio")
        archive.writestr(f"{folder}/ForeverVO_Data_Base.toc", "## Interface: 16001\n")

    installed = install_zip(zip_path, addons, folder)

    assert installed == addons / folder
    assert (installed / "Sounds" / "Quests" / "1-accept.mp3").read_bytes() == b"audio"
    assert not (installed / "Sounds" / "Quests" / "gone.mp3").exists()
    assert (installed / "Data" / "Pack.lua").is_file()


def test_install_zip_rejects_a_member_outside_the_folder(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../outside.txt", "nope")
    try:
        install_zip(zip_path, tmp_path / "addons", "ForeverVO_Data_Base")
    except SystemExit as error:
        assert "unexpected member" in str(error)
    else:
        raise AssertionError("expected SystemExit")


def test_addons_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUDITION_ADDONS", str(tmp_path))
    assert addons_from_env() == tmp_path
    monkeypatch.delenv("AUDITION_ADDONS")
    assert addons_from_env() == ADDONS_DIR
