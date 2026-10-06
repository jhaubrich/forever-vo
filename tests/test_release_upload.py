"""A pack the CurseForge API refuses is an upload failure the caller reports,
not an exit: the build is recorded all the same, for a hand upload."""

import json
from pathlib import Path

import pytest

from tools import release_pack


def test_a_refusal_is_an_upload_failure_not_an_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Refused:
        status_code = 413
        text = ""

    monkeypatch.setattr(release_pack, "curseforge_config", lambda *_: ("key", 1))
    monkeypatch.setattr(release_pack, "game_version_id", lambda _: 1)
    monkeypatch.setattr(release_pack.requests, "post", lambda *_, **__: Refused())
    zip_path = tmp_path / "pack.zip"
    zip_path.write_bytes(b"zip")
    stats = {"quests": 1, "gossip": 0, "files": {"842-accept"}}
    config = release_pack.load_config()
    with pytest.raises(release_pack.UploadFailed, match="HTTP 413"):
        release_pack.upload(
            "classic_quests", zip_path, "2026.10.06", stats, "release", config.release
        )


def test_a_release_records_its_own_state_and_leaves_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_pack, "STATE_FILE", tmp_path / "state.json")
    release_pack.record_release("classic_quests", {"stamps": {"842-accept": "t:v:1"}})
    release_pack.record_release("books", {"stamps": {}})
    release_pack.record_release("classic_quests", {"stamps": {"842-accept": "t:v:2"}})
    states = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert states == {
        "classic_quests": {"stamps": {"842-accept": "t:v:2"}},
        "books": {"stamps": {}},
    }
