"""Clips chosen by ear reach the reference in the order they were chosen."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import refclips
from tools.build_voice_references import concat_to_wav, write_concat
from tools.config import Voices


def tone(path: Path, seconds: float, hz: int) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-t",
            str(seconds),
            "-i",
            f"sine=frequency={hz}:sample_rate=24000",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )
    return path


def test_write_concat_keeps_the_order_it_was_given(tmp_path: Path) -> None:
    # deliberately not longest-first: build_reference would have sorted these and
    # dropped the short one, which is the whole reason picks bypass it
    picks = [
        tone(tmp_path / "a.ogg", 0.4, 300),
        tone(tmp_path / "b.ogg", 3.0, 400),
        tone(tmp_path / "c.ogg", 1.0, 500),
    ]
    listing = write_concat("x-male", picks, "picked.txt", tmp_path)
    assert [line.split("'")[1] for line in listing.read_text().splitlines()] == [
        str(p.resolve()) for p in picks
    ]


def test_the_picked_list_does_not_clobber_the_automatic_one(tmp_path: Path) -> None:
    one = write_concat(
        "x-male", [tone(tmp_path / "a.ogg", 0.4, 300)], "concat.txt", tmp_path
    )
    two = write_concat(
        "x-male", [tone(tmp_path / "b.ogg", 0.5, 400)], "picked.txt", tmp_path
    )
    assert one != two
    assert one.read_text() != two.read_text()


def test_concat_refuses_an_input_it_cannot_probe(tmp_path: Path) -> None:
    good, bad = tone(tmp_path / "a.ogg", 2.0, 300), tmp_path / "bad.ogg"
    bad.write_text("not an ogg", encoding="utf-8")
    out = tmp_path / "x-male.wav"
    concat_to_wav("x-male", [good, good], list_dir=tmp_path, out=out)
    assert out.exists()
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        # ffmpeg would write a 2 s wav here and exit 0, leaving a truncated reference
        concat_to_wav("x-male", [good, bad, good], list_dir=tmp_path, out=out)
    assert abs(_seconds(out) - 4.0) < 0.3  # the good build survived


def _seconds(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def test_clip_seconds_probes_once_per_build_and_survives_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import refclips

    clip = tone(tmp_path / "a.ogg", 1.5, 300)
    probes: list[Path] = []
    real = refclips.duration

    def counted(path: Path) -> float:
        probes.append(path)
        return real(path)

    monkeypatch.setattr(refclips, "duration", counted)
    cache = refclips.ClipSeconds(tmp_path / "casc")
    first = cache.get(123, "1.0.0.1", clip)
    assert abs(first - 1.5) < 0.1
    assert cache.get(123, "1.0.0.1", clip) == first
    assert len(probes) == 1
    cache.save()

    # a new process reads the lengths back and probes nothing; another build is
    # another table, since a FileDataID's file can differ between builds
    again = refclips.ClipSeconds(tmp_path / "casc")
    assert again.get(123, "1.0.0.1", clip) == first
    assert len(probes) == 1
    again.get(123, "2.0.0.2", clip)
    assert len(probes) == 2

    # saving merges into what another process wrote meanwhile
    other = refclips.ClipSeconds(tmp_path / "casc")
    other.get(456, "1.0.0.1", clip)
    other.save()
    again.save()
    merged = refclips.ClipSeconds(tmp_path / "casc")
    assert merged.get(456, "1.0.0.1", clip) == first
    assert len(probes) == 3


def test_a_race_voice_leaves_out_other_sexes_and_characters_kits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import Counter

    # skyborne-female as the client has her: her own set, Sylvanas's kit on two
    # displays, and a night elf male set on one
    monkeypatch.setattr(refclips, "speech_candidates", lambda voice: [])
    monkeypatch.setattr(
        refclips,
        "sound_set_displays",
        lambda: {"skyborne-female": Counter({3773: 86, 175: 2, 54: 1})},
    )
    monkeypatch.setattr(refclips, "named_sets", lambda: {175: [2, 3]})
    monkeypatch.setattr(
        refclips, "other_sex_set", lambda sound_id, voice: sound_id == 54
    )
    monkeypatch.setattr(refclips, "set_fdids", lambda sound_id: [sound_id * 10])
    monkeypatch.setattr(refclips, "_fetched", lambda found, voice: found)
    found = refclips.candidates("skyborne-female", Voices())
    assert [c.group for c in found] == ["set 3773"]


def test_retail_speech_is_fetched_from_the_beta_build_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # nightborne's emote table is retail's, but the beta client ships the files
    tables = {
        refclips.BETA_BUILD: {},
        refclips.RETAIL_BUILD: {"nightborne-female": [7]},
    }
    monkeypatch.setattr(refclips, "emote_speech_fdids", lambda build: tables[build])
    monkeypatch.setattr(refclips, "files_by_kit", lambda build: {})
    monkeypatch.setattr(refclips, "emote_names", lambda build: {})
    monkeypatch.setattr(refclips, "load_db2", lambda table, build: {})
    found = refclips.speech_candidates("nightborne-female")
    assert [(c.fdid, c.build) for c in found] == [
        (7, refclips.BETA_BUILD),
        (7, refclips.RETAIL_BUILD),
    ]
    assert {c.group for c in found} == {"retail speech"}


def test_a_load_reports_its_progress_file_by_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fetch(self: refclips.Candidate, voice: str) -> bool:
        return self.fdid != 2  # wago could not give the second file

    monkeypatch.setattr(refclips.Candidate, "fetch", fetch)
    monkeypatch.setattr(refclips.CLIP_SECONDS, "save", lambda: None)
    seen: list[tuple[int, int, int]] = []
    found = [refclips.Candidate("line", fdid, "1.0") for fdid in (1, 2, 3)]
    kept = refclips._fetched(
        found, "nightborne-female", progress=lambda *p: seen.append(p)
    )
    assert [c.fdid for c in kept] == [1, 3]
    assert seen[0] == (0, 3, 0)  # before anything settles: the bar can show 0 of 3
    assert max(seen) == (3, 3, 1)
    assert len(seen) == 4
