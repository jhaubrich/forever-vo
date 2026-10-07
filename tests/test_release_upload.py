"""A pack the CurseForge API refuses is an upload failure the caller reports,
not an exit: the build is recorded all the same, for a hand upload. One that
fails on the way is tried again, and the next run is due for it."""

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


def test_a_connection_failure_is_retried_then_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def broken(*_, **__):
        calls.append(1)
        raise release_pack.requests.ConnectionError("bad record mac")

    monkeypatch.setattr(release_pack, "curseforge_config", lambda *_: ("key", 1))
    monkeypatch.setattr(release_pack, "game_version_id", lambda _: 1)
    monkeypatch.setattr(release_pack.requests, "post", broken)
    zip_path = tmp_path / "pack.zip"
    zip_path.write_bytes(b"zip")
    stats = {"quests": 1, "gossip": 0, "files": {"842-accept"}}
    config = release_pack.load_config()
    with pytest.raises(release_pack.UploadFailed) as failed:
        release_pack.upload(
            "classic_quests", zip_path, "2026.10.07", stats, "release", config.release
        )
    assert failed.value.transient
    assert len(calls) == release_pack.UPLOAD_ATTEMPTS


def test_a_build_that_never_uploaded_is_due_with_nothing_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = release_pack.load_config()
    stats = {"quests": 1, "gossip": 0, "files": {"842-accept"}}
    stamps = {"842-accept": "t:v:1"}
    monkeypatch.setattr(release_pack, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(release_pack, "stage_tables", lambda *_: (tmp_path, stats))
    monkeypatch.setattr(release_pack, "pack_files", lambda _: "files")
    monkeypatch.setattr(release_pack, "file_stamps", lambda _: stamps)
    monkeypatch.setattr(release_pack, "curseforge_config", lambda *_: ("key", 1))
    built = []
    monkeypatch.setattr(
        release_pack,
        "package",
        lambda *_, **__: built.append(1) or tmp_path / "pack.zip",
    )
    uploaded = []

    def flaky(*_):
        uploaded.append(1)
        if len(uploaded) == 1:
            raise release_pack.UploadFailed("bad record mac", transient=True)

    monkeypatch.setattr(release_pack, "upload", flaky)
    args = release_pack.argparse.Namespace(
        upload=True, if_changed=True, min_new=20, max_age_days=7, release_type=None
    )
    state = {
        "date": release_pack.today().isoformat(),
        "encoding": release_pack.encoding_tag(config.release),
        "stamps": stamps,
    }
    release_pack.record_release("classic_quests", state)
    assert release_pack.release_one("classic_quests", args, config) is None
    assert not built

    release_pack.record_release("classic_quests", {**state, "uploaded": False})
    assert release_pack.release_one("classic_quests", args, config) == "retry"
    assert release_pack.release_one("classic_quests", args, config) is None
    assert len(uploaded) == 2
    states = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "uploaded" not in states["classic_quests"]
    # and once it is up, nothing changed means nothing to do again
    assert release_pack.release_one("classic_quests", args, config) is None
    assert len(uploaded) == 2
