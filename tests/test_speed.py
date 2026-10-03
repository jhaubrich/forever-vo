"""Speed: varispeed at encode time, pace and pitch together in one resample."""

from __future__ import annotations

import subprocess
from itertools import pairwise
from pathlib import Path

from tools.config import Tts, VoiceTuning
from tools.generate import encode_filters


def test_speed_is_one_resample_ahead_of_tempo_and_pitch() -> None:
    assert encode_filters() == []  # nothing neutral is ever filtered
    assert encode_filters(speed=0.8) == ["asetrate=19200,aresample=24000"]
    assert encode_filters(1.1, -1.0, 0.9) == [
        "asetrate=21600,aresample=24000",
        "atempo=1.1",
        f"rubberband=pitch={2 ** (-1 / 12):.6f}",
    ]


def test_speed_left_at_one_keeps_every_fingerprint() -> None:
    tts = Tts(voices={"x-male": VoiceTuning(exaggeration=0.7)})
    assert "speed" not in tts.differences(tts.settings_for("x-male"))
    slowed = Tts(voices={"x-male": VoiceTuning(exaggeration=0.7, speed=0.9)})
    assert slowed.differences(slowed.settings_for("x-male"))["speed"] == 0.9


def test_speed_slows_and_lowers_in_one_pass(tmp_path: Path) -> None:
    out = tmp_path / "slow.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", "1",
         "-i", "sine=frequency=400:sample_rate=24000",
         "-af", ",".join(encode_filters(speed=0.8)), str(out)],
        check=True,
    )  # fmt: skip
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert abs(float(probe) - 1.25) < 0.02  # 1 s at 0.8 speed: a quarter longer
    # and lower: 400 Hz played at 0.8 is 320 Hz; count zero crossings in a slice
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "0.2", "-t", "0.5", "-i", str(out),
         "-f", "s16le", "-ac", "1", "-ar", "24000", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    samples = [
        int.from_bytes(raw[i : i + 2], "little", signed=True)
        for i in range(0, len(raw), 2)
    ]
    crossings = sum(1 for a, b in pairwise(samples) if (a < 0) != (b < 0))
    assert abs(crossings / 2 / 0.5 - 320) < 10
