# Forever Voiceover

Voiced quests and NPC dialogue for World of Warcraft: Forever, with audio you
generate yourself from a local text-to-speech model.

**The quest text for Forever additions is crowd sourced, please help out by downloading the addon and occasionally running `/fvo export`**. 

Four addons on CurseForge: the player, and three voice packs that stack.

- **[Forever Voiceover](https://www.curseforge.com/wow/addons/forever-voiceover)**
  (`ForeverVO`) — the player, and the only one you need to start. Reads quest
  offers, turn-ins, greetings and gossip from installed voice packs and plays
  them through a talking-head frame styled after the client's own, with a queue
  you can pause, skip and reorder, and a replay button on each quest you open in
  the quest log. It also records every line it sees so new audio can be
  generated for what is still missing.
- **[Forever Voiceover Data: Base](https://www.curseforge.com/wow/addons/forever-voiceover-data-base)**
  (`ForeverVO_Data_Base`, priority 100) — the Classic lines for quests up to
  level 40, and all Classic gossip. Big, and updated almost never: this is text
  that has not changed since Classic, so once a line is voiced it stays voiced.
  Install it once and forget it.
- **[Forever Voiceover Data: Base Endgame](https://www.curseforge.com/wow/addons/forever-voiceover-data-base-endgame)**
  (`ForeverVO_Data_Base_Endgame`, priority 100) — the rest of the Classic
  lines, quests from level 41 up. The same pack as Base in two halves, because
  CurseForge caps a file at 1 GB. Both halves carry the alternate narrator
  voices for their quests.
- **[Forever Voiceover Data: Forever](https://www.curseforge.com/wow/addons/forever-voiceover-data-forever)**
  (`ForeverVO_Data_Forever`, priority 200) — everything Forever adds or
  rewrites. Small, and updated often, especially when new content drops: these
  are the lines being crowd sourced, so it grows as players run `/fvo export`.
  Its higher priority means it overrides the base pack wherever both have a
  line.

Install either pack, both, or neither — the player works on its own, it just has
nothing to say until a pack is there.

Built for the Forever client only (Camelot, interface 16001). It uses the
Retail engine APIs: the Settings panel, the addon compartment, mixins, frame
pools. No embedded libraries.

## How it works

Quest and gossip text is not in the client files; the server sends it. So the
addon captures text as you play, and the tools turn the captured lines into
audio:

1. Play. Every quest you accept or hand in and every NPC you talk to is
   recorded in `ForeverVOCaptureDB`, with the speaker's creature ID and model.
2. Log out, then `./tools/run.sh tools/ingest.py` merges the saved
   variables into `tools/data/capture.json`.
3. `./tools/run.sh tools/generate.py` picks a voice per speaker (race and
   gender from the client's display tables), synthesises the missing lines with
   Chatterbox on your GPU, and rebuilds the pack tables.
4. Restart the client (new sound files are only seen at launch) and play on.

The beta client currently does not read saved variables back at login, so run
`ingest.py` after every session; the `.bak` file gives one session of slack.

## Voice pack format

A pack is an addon that depends on ForeverVO and calls
`ForeverVO.RegisterPack(pack)`; see `ForeverVO_Data/Data/Pack.lua` and the
comment at the top of `ForeverVO/Core/Packs.lua`. Quest audio is keyed by quest
ID and event; gossip by speaker ID plus a hash of the normalised text
(`tools/textkey.py` mirrors `Util.HashText`), with a fuzzy fallback. Packs have
priorities, so a pack of new or revised lines can sit on top of a base pack.

## Setup

The tools are a [uv](https://docs.astral.sh/uv/) project (`pyproject.toml`,
`uv.lock`, `.python-version`; uv fetches the interpreter itself) and
`flake.nix` provides the rest: uv, ffmpeg, lua 5.1 and the shared libraries the
CUDA wheels expect. `tools/run.sh` is `uv run` inside that shell, so every
script, console script (`fvo-ingest`, `fvo-generate`, ... see
`pyproject.toml`) and one-liner runs against the same pins. Without nix,
`uv run tools/<script>.py` works directly with `ffmpeg` on PATH. The GPU stack
is the `tts` dependency group, on by default; `uv run --no-group tts ...`
skips it for a checkout that only ingests or releases.

```bash
./tools/run.sh tools/tts_smoke.py            # CUDA check, writes tools/smoke.wav
./tools/run.sh tools/build_voice_references.py   # reference clips from the client's own voice lines

B="$HOME/Faugus/battlenet/drive_c/Program Files (x86)/World of Warcraft/_classic_beta_/Interface/AddOns"
ln -s "$PWD/ForeverVO" "$B/ForeverVO"
ln -s "$PWD/ForeverVO_Data" "$B/ForeverVO_Data"
```

Provide `tools/voices/narrator.wav` (10 to 20 s of clean speech) for quests and
gossip from items and objects. Those lines are generated again in each voice
listed as `narrator_alternates` in `forever-vo.toml`, under
`Sounds/<Quests|Gossip>/Narrator/<voice>/`, so
players can pick the narrator they prefer in the options. The extra passes sort after
every line that has no audio at all; `--narrator-voices none` leaves them out
of a run and `--narrator-only` makes a run of nothing else.

## In game

- `/fvo` opens the options. `/fvo pause|resume|skip|clear|replay|queue|head|reset|narrator|status|debug`.
- The narrator reads the lines with no speaker to voice them: quests and
  chatter from objects, items and signs. Options > Audio > Narrator voice picks
  which voice that is, from whatever the pack carries; `/fvo narrator` cycles.
- Right-click the talking head to skip; the X clears the queue; "Queue" shows
  what is waiting; drag to move (lockable in options).
- The addon compartment entry (next to the minimap) has the same controls.
- Opening a quest in the quest log puts a Play button beside Back, for quests
  that have audio.

## Credits

MIT licensed. Interface concept after
[VoiceOver](https://github.com/mrthinger/wow-voiceover) by mrthinger and
contributors; the button textures are theirs (public domain). Client data
comes from [wago.tools](https://wago.tools). Speech by
[Chatterbox](https://github.com/resemble-ai/chatterbox). Voice packs are fan
content: Blizzard's text, voices generated from the game's own recordings,
shared for non-commercial use.

## Contributing lines

Quest and gossip text only exists on the server, so the pack can only grow
from what players see. With the addon on, play (the new zones matter most),
then type `/fvo export`, press Ctrl+C, and paste the string into a new
"Contribute captured lines" issue:
https://github.com/quinn-dougherty/forever-vo/issues/new?template=capture.yml.
A bot decodes it into `captures/`, reacts with 🚀, closes the issue, and the
next pack build voices it. Your character name is removed before export.
(Pasting it as a comment on the older inbox, issue #1, still works too.)
