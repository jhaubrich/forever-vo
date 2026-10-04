"""A picked reference is rebuilt from its saved pick before anything is generated.

The picks travel in git and the wavs do not, so a merged re-pick used to restage its
voice from the old wav (2,042 files on 2026-10-03). Each picked wav now carries a
record of what it was built from, and generate.py rebuilds any that disagree.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tools import build_voice_references as refs
from tools import generate
from tools.config import VoiceSources, load_config


@pytest.fixture
def voices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An empty voices folder, and a concat that writes a stand-in wav."""
    monkeypatch.setattr(refs, "VOICES_DIR", tmp_path)
    monkeypatch.setattr(generate, "VOICES_DIR", tmp_path)

    def concat(voice, paths, list_name, *, list_dir=None, out=None, gaps=None):
        out = out or tmp_path / f"{voice}.wav"
        out.write_bytes(b"RIFF" + ",".join(p.stem for p in paths).encode())
        return out

    monkeypatch.setattr(refs, "concat_to_wav", concat)
    monkeypatch.setattr(refs, "duration", lambda path: 1.0)
    yield tmp_path


def clip_paths(tmp_path: Path, clips: list[int]) -> list[Path]:
    paths = [tmp_path / "raw" / f"{c}.ogg" for c in clips]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"OggS")
    return paths


def test_gaps_compare_padded() -> None:
    assert refs.pick_signature([1, 2, 3], None, [0.1]) == refs.pick_signature(
        [1, 2, 3], None, [0.1, 0.0]
    )
    assert refs.pick_signature([1, 2], None, []) != refs.pick_signature(
        [1, 2], None, [0.2]
    )


def test_a_build_records_its_pick(voices: Path) -> None:
    pick = VoiceSources(clips=[11, 12], gaps=[0.1])
    assert not refs.picked_reference_current("orc-male-guard", pick)  # no wav yet
    refs.build_picked_reference(
        "orc-male-guard", clip_paths(voices, [11, 12]), [0.1], clips=[11, 12]
    )
    assert refs.picked_reference_current("orc-male-guard", pick)
    # a re-pick merged from elsewhere: the wav on disk no longer matches
    assert not refs.picked_reference_current(
        "orc-male-guard", VoiceSources(clips=[12, 11], gaps=[0.1])
    )


def test_a_wav_with_no_record_is_not_current(voices: Path) -> None:
    (voices / "skyborne-male.wav").write_bytes(b"RIFF")
    assert not refs.picked_reference_current(
        "skyborne-male", VoiceSources(clips=[1, 2])
    )


def test_clips_read_off_file_names(voices: Path) -> None:
    refs.build_picked_reference("troll-male-dark", clip_paths(voices, [5, 6]))
    assert refs.picked_reference_current("troll-male-dark", VoiceSources(clips=[5, 6]))


def test_generate_rebuilds_a_stale_pick_and_reports_a_failed_one(
    voices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config()
    sources = {
        "orc-male-guard": VoiceSources(clips=[11, 12]),
        "goblin-male-zany": VoiceSources(clips=[21, 22]),
    }
    monkeypatch.setattr(config.voices, "sources", sources)
    built: list[str] = []

    def fake_build_pick(voice: str, pick: VoiceSources):
        built.append(voice)
        if voice == "goblin-male-zany":
            return None  # a clip gone from wago
        return refs.build_picked_reference(
            voice, clip_paths(voices, pick.clips), clips=pick.clips
        )

    monkeypatch.setattr(generate, "build_pick", fake_build_pick)

    assert generate.ensure_picked_references(config, dry_run=True) == set()
    assert built == []  # a dry run only says what it would do

    assert generate.ensure_picked_references(config) == {"goblin-male-zany"}
    assert built == ["goblin-male-zany", "orc-male-guard"]

    built.clear()
    generate.ensure_picked_references(config)
    assert built == ["goblin-male-zany"]  # orc-male-guard is current now
