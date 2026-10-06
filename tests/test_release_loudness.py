"""Release files come out at one loudness, whatever the take's (#513)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tools.config import Release
from tools.release_pack import (
    LOUDNORM,
    encoding_tag,
    loudnorm_filter,
    transcode,
)


def take(path: Path, seconds: float, volume: str) -> Path:
    """A speech-length take at a given level: a tone with a slow wobble, so the
    loudness meter has something that rises and falls."""
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
            "sine=frequency=220:sample_rate=24000",
            "-af",
            f"tremolo=f=3:d=0.5,volume={volume}",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )
    return path


def loudness(path: Path) -> float:
    second = loudnorm_filter(path)
    assert second, "nothing measured"
    return float(second[1].split("measured_I=")[1].split(":")[0])


def duration(path: Path) -> float:
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
    return float(out.stdout)


def test_a_quiet_and_a_loud_take_come_out_alike(tmp_path: Path) -> None:
    quiet = take(tmp_path / "quiet.wav", 4, "-30dB")
    loud = take(tmp_path / "loud.wav", 4, "-6dB")
    assert loudness(loud) - loudness(quiet) > 20
    out = []
    for src in (quiet, loud):
        dst = tmp_path / "out" / f"{src.stem}.mp3"
        transcode(src, dst, "32k")
        out.append(loudness(dst))
        # The addon pages the text against the file's length
        assert abs(duration(dst) - duration(src)) < 0.1
    assert all(abs(level + 16) < 1.5 for level in out), out


def test_silence_is_encoded_as_it_is(tmp_path: Path) -> None:
    silent = take(tmp_path / "silent.wav", 2, "0")
    assert loudnorm_filter(silent) == []
    dst = tmp_path / "silent.mp3"
    transcode(silent, dst, "32k")
    assert dst.exists()


def test_the_loudness_is_part_of_the_encoding() -> None:
    # A new encoding re-releases every pack, which is how the files already
    # released at their own loudness get replaced
    assert LOUDNORM in encoding_tag(Release())
