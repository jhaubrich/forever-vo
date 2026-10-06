"""A pack the CurseForge API refuses is an upload failure the caller reports,
not an exit: the build is recorded all the same, for a hand upload."""

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
            "base", zip_path, "2026.10.06", stats, "release", config.release
        )


def test_a_release_records_its_state_and_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("STATE_FILE", "BASELINE_FILE"):
        monkeypatch.setattr(release_pack, name, tmp_path / f"{name.lower()}.json")
    release_pack.record_release(
        "base",
        {
            "state": {"version": "2026.10.06", "files": ["842-accept"]},
            "baseline": {"842-accept": "t:v:1.0"},
        },
    )
    release_pack.record_release(
        "base_endgame",
        {"state": {"version": "2026.10.06", "files": []}, "baseline": {}},
    )
    assert release_pack.load_baseline() == {"842-accept": "t:v:1.0"}
    release_pack.record_release("delta", {"state": {"version": "x"}, "baseline": None})
    assert release_pack.load_baseline() == {"842-accept": "t:v:1.0"}
