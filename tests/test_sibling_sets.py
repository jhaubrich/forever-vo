"""A set with no clip of its own reads its sibling's: the same actor cast again."""

from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools import build_voice_references, soundpaths, wowdata


@pytest.fixture
def cast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Night elf males as the client has them, in miniature: 59 the main standard set
    with a clip, 121 its sibling (59's files and two more), 55 the warrior set, and two
    skyborne-like sets in one unnamed folder with no file in common."""
    counts = {"nightelf-male": Counter({59: 72, 55: 68, 121: 34, 3776: 9, 3775: 8})}
    folder = {
        59: "nightelfmalestandardnpc",
        121: "nightelfmalestandardnpc",
        55: "nightelfmalewarriornpc",
        3776: "7478487",
        3775: "7478487",
    }
    files = {
        59: list(range(14)),
        121: [*range(14), 100, 101],
        55: [200, 201],
        3776: [300, 301],
        3775: [400, 401],
    }
    names = {
        59: "nightelf-male-standard",
        55: "nightelf-male-warrior",
        121: "nightelf-male-s121",
        3776: "nightelf-male-s3776",
        3775: "nightelf-male-s3775",
    }
    monkeypatch.setattr(wowdata, "VOICES_DIR", tmp_path)
    monkeypatch.setattr(wowdata, "sound_set_displays", lambda: counts)
    monkeypatch.setattr(wowdata, "archetype_names", lambda voice: names)
    monkeypatch.setattr(wowdata, "display_sound_set", lambda display: display)
    monkeypatch.setattr(soundpaths, "folders", lambda: folder)
    monkeypatch.setattr(build_voice_references, "set_fdids", lambda s: files[s])
    wowdata.sibling_sets.cache_clear()
    for name in ("nightelf-male", "nightelf-male-standard", "nightelf-male-s3776"):
        (tmp_path / f"{name}.wav").write_bytes(name.encode())
    yield tmp_path
    wowdata.sibling_sets.cache_clear()


def test_a_clipless_set_reads_its_siblings_clip(cast: Path) -> None:
    # the display id stands in for its set here
    assert wowdata.archetype_voice("nightelf-male", 121) == "nightelf-male-standard"
    assert wowdata.archetype_voice("nightelf-male", 59) == "nightelf-male-standard"
    # its own clip, once built, wins over the borrowed one
    (cast / "nightelf-male-s121.wav").write_bytes(b"its own")
    assert wowdata.archetype_voice("nightelf-male", 121) == "nightelf-male-s121"


def test_a_shared_folder_without_shared_recordings_is_no_sibling(cast: Path) -> None:
    assert wowdata.sibling_sets("nightelf-male", 3775) == ()
    assert wowdata.archetype_voice("nightelf-male", 3775) is None
    # nor does a set with neither clip nor sibling borrow anything
    assert wowdata.archetype_voice("nightelf-male", 55) is None
