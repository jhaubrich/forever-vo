"""Builds reference clips for voice cloning straight from the client's own audio.

chatterbox reads only the first 6 s (t3) and 10 s (s3gen) of a reference and then
continues it, so the head of a clip is the voice and everything past 10 s only nudges
an averaged embedding. Each clip is therefore composed rather than pooled:

  head  a few seconds of the race's spoken emote lines (EmotesTextSound: JOKE, FLIRT),
        a different slice per voice so archetypes of one race do not collapse together
  tail  that speaker's own NPC greetings, starting inside the 10 s window so the
        archetype colours the voice

Blizzard casts every creature display with an NPCSounds set - a young one, a warrior,
an elder - and wowdata.voice_for_npc resolves through it, so the outputs are:

    tools/voices/<race>-<gender>.wav          the race's dominant archetype
    tools/voices/<race>-<gender>-s<set>.wav   the other archetypes NPCs are cast with
    tools/voices/npc-<displayID>.wav          sets used by <= 3 displays: one character

    ./tools/run.sh tools/build_voice_references.py            # all voices
    ./tools/run.sh tools/build_voice_references.py tauren     # one race
    ./tools/run.sh tools/build_voice_references.py --named    # the named-NPC clips

Everything is fetched through wago.tools by FileDataID, so no local CASC extraction is
needed. Raw clips are kept under tools/voices/raw/.
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from tools.config import GENDER_DICT, RACE_DICT, VOICES_DIR
from tools.wowdata import dominant_sound_set, fetch_file, load_db2, sound_set_displays

RAW_DIR = VOICES_DIR / "raw"
TARGET_SECONDS = 20.0
MAX_CLIP_SECONDS = 15.0   # long enough for a spoken emote line; a bark is 1-3 s
MAX_FILES_PER_VOICE = 40   # download cap per voice; greeting kits repeat a lot


def files_by_kit() -> dict[int, list[int]]:
    result: dict[int, list[int]] = defaultdict(list)
    for entry in load_db2("SoundKitEntry").values():
        result[int(entry["SoundKitID"])].append(int(entry["FileDataID"]))
    return result


# A race has only so many spoken emote lines to hand out - scourge-male has 11 -
# and every archetype minted takes a slice of them, so a long tail of archetypes
# leaves the last ones with a 3 s head spliced from short clips. Better a few good
# personalities than many thin ones: keep the archetypes the most NPCs are cast
# with, and let everyone else fall back to the race voice. By rank rather than an
# absolute count, because races differ by an order of magnitude - human-male's third
# set has 245 displays where tauren-female's has 26.
MAX_ARCHETYPES_PER_VOICE = 3
MIN_ARCHETYPE_DISPLAYS = 5   # below this it is one character, and npc-<displayID> covers it

# Emotes whose recordings are spoken sentences. The rest of a race's emote set is
# wordless - crying, whistling, a chicken impression - and sorting by length puts
# exactly those at the head of a reference, where they become the voice.
SPEECH_EMOTES = {"JOKE", "FLIRT"}



def emote_speech_fdids() -> dict[str, list[int]]:
    """voice name -> FileDataIDs of that race's spoken emote lines.

    NPC greeting kits are barks: "Hello there!" in one to three seconds, disjoint,
    with nothing about how the speaker forms a sentence. EmotesTextSound has the
    same actors telling jokes and flirting - four to thirteen seconds of connected
    speech - which is what chatterbox needs to clone prosody rather than timbre
    alone. VocalUISounds adds the spoken UI errors ("I can't carry any more").
    """
    kits = files_by_kit()
    names = {int(key): row["Name"] for key, row in load_db2("EmotesText").items()}
    voices: dict[str, list[int]] = defaultdict(list)
    for row in load_db2("EmotesTextSound").values():
        race = RACE_DICT.get(int(row.get("RaceID") or 0))
        gender = GENDER_DICT.get(int(row.get("SexID") or 0))
        if not race or not gender:
            continue
        if names.get(int(row.get("EmotesTextID") or 0)) not in SPEECH_EMOTES:
            continue
        for fdid in kits.get(int(row.get("SoundID") or 0), []):
            if fdid not in voices[f"{race}-{gender}"]:
                voices[f"{race}-{gender}"].append(fdid)
    return dict(voices)


def set_fdids(sound_id: int) -> list[int]:
    """The hello and goodbye files of one NPCSounds set (SoundID_2 is "pissed", _3 ack)."""
    kits = files_by_kit()
    npc_sounds = load_db2("NPCSounds")
    fdids: list[int] = []
    for col in ("SoundID_0", "SoundID_1"):
        for fdid in kits.get(int(npc_sounds[sound_id].get(col) or 0), []):
            if fdid not in fdids:
                fdids.append(fdid)
    return fdids


def npc_greeting_fdids() -> dict[str, list[int]]:
    """voice name -> FileDataIDs, from the archetype most of that race is cast with.

    Blizzard gives each race and gender several NPCSounds sets - a young one, a
    warrior, an elder - and each creature display names the one it was cast with.
    Pooling them all and taking the longest clips put a *rare* archetype at the head
    of nearly every reference: chatterbox reads only the first 6 s (t3) and 10 s
    (s3gen) of a clip, and one-off character sets have the longest lines, so seven of
    23 voices were cloned from a set used by a single creature display (eleven if you
    count heads whose sound kit is shared with a rare set). tauren-female came from
    set 172, an elder with one display in the game, whose clips are two of the first
    9.9 s and three of the six; set 70, cast for 65 displays, never reached the part
    of the clip that matters.

    So the plain <race>-<gender> clip now comes from the archetype the most NPCs
    actually use, and archetype_fdids() builds the others alongside it.
    """
    voices: dict[str, list[int]] = {}
    for voice in sound_set_displays():
        sound_id = dominant_sound_set(voice)
        if sound_id:
            fdids = set_fdids(sound_id)
            if fdids:
                voices[voice] = fdids
    return voices


def archetype_fdids() -> dict[str, list[int]]:
    """`<race>-<gender>-s<set>` -> files, one entry per archetype a race really uses.

    wowdata.voice_for_npc prefers these when a speaker's display names the set, so
    an elder is read by the elder's voice instead of lending her delivery to every
    young NPC of her race.
    """
    voices: dict[str, list[int]] = {}
    for voice, counts in sound_set_displays().items():
        kept = [(sid, n) for sid, n in counts.most_common(MAX_ARCHETYPES_PER_VOICE)
                if n >= MIN_ARCHETYPE_DISPLAYS]
        for sound_id, _ in kept:
            fdids = set_fdids(sound_id)
            if fdids:
                voices[f"{voice}-s{sound_id}"] = fdids
    return voices


NAMED_MAX_DISPLAYS = 3   # a greeting kit shared by this few models belongs to a named NPC


def named_npc_fdids() -> dict[str, list[int]]:
    """voice name npc-<displayID> -> greeting FileDataIDs for NPCs with their own recorded lines
    (Varimathras, Thrall, Sylvanas, ...). Race voices come from kits shared by many models."""
    kits = files_by_kit()
    npc_sounds = load_db2("NPCSounds")
    displays_by_sound: dict[int, list[int]] = defaultdict(list)
    for display_id, row in load_db2("CreatureDisplayInfo").items():
        sound_id = int(row.get("NPCSoundID") or 0)
        if sound_id and sound_id in npc_sounds:
            displays_by_sound[sound_id].append(display_id)
    voices: dict[str, list[int]] = {}
    for sound_id, displays in displays_by_sound.items():
        if len(displays) > NAMED_MAX_DISPLAYS:
            continue
        fdids: list[int] = []
        for col in ("SoundID_0", "SoundID_1", "SoundID_2"):  # hello, goodbye, pissed
            for fdid in kits.get(int(npc_sounds[sound_id].get(col) or 0), []):
                if fdid not in fdids:
                    fdids.append(fdid)
        if fdids:
            for display_id in displays:
                voices[f"npc-{display_id}"] = fdids
    return voices


def skyborne_fdids() -> dict[str, list[int]]:
    """VocalUISounds.NormalSoundID_0 is the male kit, _1 the female kit."""
    kits = files_by_kit()
    voices: dict[str, list[int]] = defaultdict(list)
    for row in load_db2("VocalUISounds").values():
        if int(row["RaceID"]) not in (95, 96):
            continue
        for gender, col in (("male", "NormalSoundID_0"), ("female", "NormalSoundID_1")):
            for fdid in kits.get(int(row.get(col) or 0), []):
                if fdid not in voices[f"skyborne-{gender}"]:
                    voices[f"skyborne-{gender}"].append(fdid)
    return voices


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out or 0)


SPEECH_HEAD_CLIPS = 2        # splices inside the window cost delivery; keep the head to a few
# An archetype is only worth minting if it can get a real head. Rated by ear over
# twelve races, every voice with 4.8 s or more of speech at the head was preferred to
# the shipped pooled one, and every voice with 4.0 s or less was worse - troll-male at
# 3.4 s and troll-female at 3.2 s lost the Jamaican delivery the way dwarves lost the
# Scottish one (#18), because too little connected speech lets the model fall back on
# its own neutral prior. Below the bar the speaker falls back to the race voice, which
# takes the longest slice and so always clears it.
ARCHETYPE_MIN_HEAD = 4.5
# s3gen reads 10 s. Spending it all on shared emote speech left every archetype
# sounding like the race's emote actor and nothing else; half of it leaves room for
# the speaker's own clips inside the window, which is what makes one archetype
# different from another. 5.5 s measured best in the sweep: a 0.071 gap between two
# scourge archetypes against 0.104 for barks alone, without the flat bark delivery.
SPEECH_HEAD_SECONDS = 5.5


def speech_heads(sources: dict[str, list[int]]) -> dict[str, tuple[list[int], int]]:
    """{voice: (that race's spoken emote files, which slice of them this voice leads with)}.

    Barks clone a voice but not a delivery, and a head of spoken emote lines fixes
    that - but handing every archetype of a race the same speech collapses them into
    one voice (all seven scourge-male clips came out byte-identical). So each
    archetype leads with different lines from the same actor: same race, same timbre,
    a different performance continued. Measured on scourge-male, three archetypes
    built this way sit 0.978 within a personality against 0.892 across them, where
    sharing the head left a gap of 0.014.

    The slices are handed out most-used archetype first, so the longest lines go to
    the voices the most creature displays use: a head of one long clip beats two, and
    two beat three - the voice rated "robotic" in testing was the one whose head was
    three short clips spliced together.
    """
    speech = emote_speech_fdids()
    by_voice: dict[str, list[str]] = defaultdict(list)
    for name in sources:
        by_voice[base_voice(name)].append(name)
    heads: dict[str, tuple[list[int], int]] = {}
    for race_gender, names in by_voice.items():
        pool = speech.get(race_gender)
        if not pool:
            continue        # no spoken emotes for this race; it stays bark-led
        counts = sound_set_displays().get(race_gender, {})

        def displays(name: str) -> int:
            if name == race_gender:
                # The plain voice picks first because it speaks for the most NPCs on
                # this client: GetDisplayInfo never returns anything on Forever
                # (issue #2), so speakers resolve through modelFileID, which names no
                # display and therefore no archetype. Ranking it by the displays of
                # its sub-threshold sets put it last of four and left tauren-female
                # heading with a 2.0 s clip.
                return 1 << 30
            return counts.get(int(name.rsplit("-s", 1)[1]), 0)

        if len(pool) < len(names) * SPEECH_HEAD_CLIPS:
            print(f"{race_gender}: {len(pool)} spoken lines for {len(names)} voices, "
                  f"so some share a head and will sound alike")
        for index, name in enumerate(sorted(names, key=displays, reverse=True)):
            heads[name] = (pool, index)
    return heads


def base_voice(name: str) -> str:
    """`tauren-female-s70` -> `tauren-female`; anything else unchanged."""
    return name.rsplit("-s", 1)[0] if re.fullmatch(r".+-s\d+", name) else name


def build_reference(voice: str, files: list[Path], head: list[Path] | None = None,
                    rotation: int = 0) -> Path | None:
    """Concatenates a head of connected speech and a tail of the voice's own clips.

    chatterbox reads the first 6 s (t3) and 10 s (s3gen) of a reference and continues
    it, so the head is the voice and everything past 10 s only nudges an averaged
    embedding. The head is spoken emote lines - this voice's slice of them - and the
    tail is the speaker's own greetings, which start inside the 10 s window so the
    archetype colours the voice rather than only the averaged embedding.
    """
    def usable(paths: list[Path]) -> list[tuple[float, Path]]:
        # The old 8 s ceiling was there to skip long barks, and it also threw away
        # every spoken emote line - scourge-male's 12.8 s joke, the longest Forsaken
        # speech in the client, was excluded from the reference it should have headed
        return sorted(((duration(p), p) for p in paths), reverse=True)

    chosen, total = [], 0.0
    if head:
        # Only clips that can fit the head budget, and the rotation is applied to
        # those. Rotating the unfiltered pool looked right and was not: every voice
        # skipped the same too-long clips and landed on the same largest one that
        # fit, so 13 archetypes came out byte-identical to their plain voice.
        pool = [(d, p) for d, p in usable(head) if 0.8 <= d <= SPEECH_HEAD_SECONDS]
        offset = (rotation * SPEECH_HEAD_CLIPS) % len(pool) if pool else 0
        for d, p in pool[offset:] + pool[:offset]:
            # Fit under the cap rather than stopping once past it. Checking after the
            # append let two long clips make a 20 s "head", which pushed the voice's
            # own clips past TARGET_SECONDS and out of the window the model reads -
            # the archetype then contributed nothing to how it sounded.
            if len(chosen) >= SPEECH_HEAD_CLIPS or total + d > SPEECH_HEAD_SECONDS:
                continue
            chosen.append(p)
            total += d
    for d, p in usable(files):
        if not (0.8 <= d <= 8.0):       # skip grunts and long barks
            continue
        chosen.append(p)
        total += d
        if total >= TARGET_SECONDS:
            break
    if not chosen or (total < 4.0 and voice.startswith("npc-")):
        print(f"{voice}: not enough usable audio ({total:.1f}s)")
        return None
    list_file = RAW_DIR / voice / "concat.txt"
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in chosen), encoding="utf-8")
    out = VOICES_DIR / f"{voice}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-ac", "1", "-ar", "24000", "-af", "loudnorm", str(out)],
        check=True,
    )
    head_seconds = sum(duration(p) for p in chosen[:SPEECH_HEAD_CLIPS]) if head else 0.0
    if re.fullmatch(r".+-s\d+", voice) and head_seconds < ARCHETYPE_MIN_HEAD:
        # Not enough speech to hold a delivery; wowdata.archetype_voice falls back to
        # the race voice when the clip is absent, and that one has the longest head
        out.unlink(missing_ok=True)
        print(f"{out.name}: head only {head_seconds:.1f}s, leaving these NPCs on the race voice")
        return None
    print(f"{out.name}: {len(chosen)} clips, {total:.1f}s"
          + (f", head {head_seconds:.1f}s" if head else " (no speech available)"))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    wanted = set(argv)
    named_only = "--named" in wanted
    wanted.discard("--named")
    sources = {} if named_only else npc_greeting_fdids()
    if not named_only:
        sources.update(archetype_fdids())
        heads = speech_heads(sources)
        for voice, fdids in skyborne_fdids().items():
            # The Skyborne have no spoken emotes, but VocalUISounds is the same thing
            # for them: recorded sentences rather than barks. It goes in as the head,
            # leaving the greeting kit (set 3776 has 78 displays) as the tail. It used
            # to be prepended to sources instead, where MAX_FILES_PER_VOICE truncation
            # dropped every greeting file and a third of the VocalUI ones.
            heads.setdefault(voice, (fdids, 0))
            sources.setdefault(voice, [])
    else:
        heads = {}
    if named_only or "npc" in wanted:
        sources.update(named_npc_fdids())
        wanted.discard("npc")

    if not wanted and not named_only:
        keep = set(sources) | {p.stem for p in VOICES_DIR.glob("npc-*.wav")}
        for stale in sorted(VOICES_DIR.glob("*-s*.wav")):
            if stale.stem not in keep:
                # wowdata.archetype_voice resolves on existence alone, so a clip left
                # behind by an earlier set of constants still casts NPCs
                print(f"removing stale {stale.name}")
                stale.unlink()
    for voice in sorted(sources):
        race = voice.split("-")[0]
        if wanted and race not in wanted and voice not in wanted:
            continue
        folder = RAW_DIR / voice
        paths = []
        for fdid in sources[voice][:MAX_FILES_PER_VOICE]:
            try:
                paths.append(fetch_file(fdid, folder / f"{fdid}.ogg"))
            except FileNotFoundError as e:
                print("skip:", e)
        head_paths = []
        pool, rotation = heads.get(voice, ([], 0))
        for fdid in pool[:MAX_FILES_PER_VOICE]:
            try:
                head_paths.append(fetch_file(fdid, RAW_DIR / base_voice(voice) / f"{fdid}.ogg"))
            except FileNotFoundError as e:
                print("skip:", e)
        print(f"{voice}: {len(paths)} clips downloaded, {len(head_paths)} speech clips")
        if paths or head_paths:
            build_reference(voice, paths, head_paths, rotation)
    return 0


if __name__ == "__main__":
    sys.exit(main())
