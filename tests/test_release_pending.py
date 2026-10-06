"""A pack the CurseForge API refuses waits on a hand upload, recorded nowhere,
until `release_pack.py <pack> --confirm`."""

import json
from pathlib import Path

import pytest

from tools import release_pack


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("STATE_FILE", "BASELINE_FILE", "PENDING_FILE"):
        monkeypatch.setattr(release_pack, name, tmp_path / f"{name.lower()}.json")
    return tmp_path


def record(version: str) -> dict:
    return {
        "state": {"version": version, "files": ["842-accept"]},
        "baseline": {"842-accept": "t:v:1.0"},
    }


def test_a_refused_pack_is_recorded_only_once_confirmed(files: Path) -> None:
    release_pack.save_pending({"base": record("2026.10.06")})
    assert not release_pack.STATE_FILE.exists()
    assert release_pack.load_baseline() is None

    assert release_pack.confirm("base")
    state = json.loads(release_pack.STATE_FILE.read_text())
    assert state["base"]["version"] == "2026.10.06"
    baseline = json.loads(release_pack.BASELINE_FILE.read_text())
    assert baseline["base"] == {"842-accept": "t:v:1.0"}
    # Nothing left waiting, so the file the nightly checks for is gone
    assert not release_pack.PENDING_FILE.exists()
    assert not release_pack.confirm("base")


def test_confirming_one_pack_leaves_the_others_waiting(files: Path) -> None:
    release_pack.save_pending(
        {"base": record("2026.10.06"), "base_endgame": record("2026.10.06")}
    )
    assert release_pack.confirm("base_endgame")
    assert list(release_pack.load_pending()) == ["base"]


def test_a_refusal_is_an_upload_failure_not_an_exit(
    files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Refused:
        status_code = 413
        text = ""

    monkeypatch.setattr(release_pack, "curseforge_config", lambda *_: ("key", 1))
    monkeypatch.setattr(release_pack, "game_version_id", lambda _: 1)
    monkeypatch.setattr(release_pack.requests, "post", lambda *_, **__: Refused())
    zip_path = files / "pack.zip"
    zip_path.write_bytes(b"zip")
    stats = {"quests": 1, "gossip": 0, "files": {"842-accept"}}
    config = release_pack.load_config()
    with pytest.raises(release_pack.UploadFailed, match="HTTP 413"):
        release_pack.upload(
            "base", zip_path, "2026.10.06", stats, "release", config.release
        )
