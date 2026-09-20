# CLAUDE.md — Forever Voiceover

Notes for agents working in this repo. Read this before touching anything.

## What this is

Voiced quests and NPC dialogue for **World of Warcraft: Forever** (the
Classic-plus client, codename Camelot). Two addons plus a Python pipeline:

- `ForeverVO/` — the player addon. Lua, Retail-engine APIs only, no libraries.
- `ForeverVO_Data/` — the voice pack (generated Lua tables + mp3s). Only the
  tables are in git; the audio lives on the maintainer's machine and ships as
  a separate zip.
- `tools/` — capture ingestion, bulk text sources, voice reference building,
  Chatterbox TTS generation, pack table writer, packaging helpers.
- `captures/` — community `/fvo export` submissions, committed by a GitHub
  Action from comments on the pinned issue #1.

Owner: Quinn Dougherty (quinn@for-all.dev). CurseForge projects: addon 1705010,
delta pack 1705094, base pack 1705100 (IDs in tools/config.py; API key only in the gitignored .env). Addon
slug `forever-vo`, display name "Forever Voiceover". License MIT; voice packs
are non-commercial fan content (Blizzard's text, voices cloned from the
game's own recordings).

## The client, and why so much is unusual

- Product `wow_classic_beta`, version 1.60.1, **Interface 16001**, TOC suffix
  `_Camelot`. It runs the **Retail engine** (`WOW_PROJECT_ID ==
  WOW_PROJECT_MAINLINE`) with Classic data. Port from Retail code paths, never
  from Classic ones.
- Blizzard's UI source for this exact build is in the Gethe mirror, branch
  `forever` (`github.com/Gethe/wow-ui-source`). A sparse checkout was used
  at `~/Projects/wow-ui-source`. Check there before assuming an API or frame
  exists. Camelot-specific overrides live in `*/Camelot/` folders.
- `docs/forever_api.json` is a captured list of the client's global
  functions, frames and `C_*` namespaces. `uv run tools/apicheck.py` diffs
  the addon against it. Run it after any Lua change.
- Removed globals that bite: `MouseIsOver` (use `frame:IsMouseOver()`),
  `SetDesaturation` (use `texture:SetDesaturated()`),
  `InterfaceOptions_AddCategory` (use the Settings API),
  `GetGossipText` (use `C_GossipInfo.GetText()`). `GameTooltip:SetText`
  rejects the old 6-argument form.
- **Saved variables are written on logout/reload but never read back** on
  this beta. Every session starts from defaults. Consequences: settings reset
  each login (that is why the unvoiced-line reminders are on by default), the
  welcome popup's "seen" flag lives in an addon-registered CVar instead, and
  the capture only ever holds one session, so it is ingested on every write.
- The addon compartment exists in the code but does not show on the Camelot
  minimap skin, hence our own minimap button.
- Quest and gossip **text is not in the client files**. The server sends it.
  Client tables on wago.tools (`QuestV2`, `BroadcastText`) carry no usable
  text. Text comes only from: in-game capture, the client's own quest cache
  (`Cache/WDB/enUS/questcache.wdb`, offers only), and the open Classic
  database snapshot (VMaNGOS, for unchanged Classic content).

## Addon conventions

- Every file starts `local _, ns = ...`; only `ForeverVO` (the namespace) and
  the three compartment handlers are globals. Modules register with
  `ns.OnInit`/`ns.OnLogin`.
- UI follows Blizzard's own frames: the talking head is a rebuild of
  `TalkingHeadFrame` (same atlases, anchors, animations); buttons use
  `UIPanelButtonTemplate`; options use `Settings.RegisterAddOnSetting` and
  friends; the queue is a `CallbackRegistryMixin`; rows use frame pools.
  Keep that discipline: no embedded libraries, native look.
- Voice pack format is documented at the top of `ForeverVO/Core/Packs.lua`.
  Quests are keyed by ID and event with durations; gossip by speaker key
  (creature ID, negative for game objects) plus a text hash, with a Jaccard
  fuzzy fallback.
- **The text hash must stay identical in Lua and Python**:
  `Util.NormalizeText`/`Util.HashText` in `Core/Util.lua` and
  `tools/textkey.py`. Parity was tested; if you touch one, touch the other
  and re-test.
- `luac -p` every changed Lua file (`nix shell nixpkgs#lua5_1 -c luac -p`).
  There is no in-game test harness; the owner tests by `/reload`.

## Pipeline (tools/)

Scripts carry inline `# /// script` metadata and run with `uv run`.
`tools/run.sh` wraps that and, on NixOS, supplies Python, uv, ffmpeg and the
shared libraries CUDA wheels need. Use `./tools/run.sh tools/<x>.py`.

Data flow (all JSON is the source of truth; `ForeverVO_Data/Data/*.lua` is a
build artifact, never hand-edited):

1. `ingest.py` merges `WTF/Account/*/SavedVariables/ForeverVO.lua` and
   `captures/*.json` into `tools/data/capture.json`.
2. `classicdb.py` exports the VMaNGOS SQLite snapshot to
   `tools/data/bulk/classic.json` (ignored, 7 MB, regenerable).
   `wdbcache.py` decodes the beta quest cache to `bulk/questcache.json`
   (versioned). Precedence when merging: capture > questcache > classic.
3. `generate.py` picks a voice per speaker, synthesises with Chatterbox on the
   GPU, writes mp3s under `ForeverVO_Data/Sounds/`, rebuilds the tables every
   25 files, and records the voice used per file in `sound_index.json` so a
   file is regenerated when its resolved voice changes (e.g. a guessed
   Skyborne male giver turns out female once captured).
4. `build_voice_references.py` makes cloning clips under `tools/voices/`
   from the client's own audio via wago.tools: race voices from shared NPC
   greeting kits, Skyborne from `VocalUISounds`, and `--named` for NPCs whose
   greeting kit is theirs alone (Varimathras, Thrall, Sylvanas, ...).
   `wowdata.voice_for_npc` prefers `npc-<displayID>.wav`, then race+gender
   from `CreatureDisplayInfoExtra`, then zone hints, then narrator.

Voice quality notes: Chatterbox on an RTX 3080 does ~6 s of audio in ~5 s
with the game closed, roughly 3x slower with it open. Perth (the watermarker)
needs `setuptools<81`. Text cleaning rules mirror the original VoiceOver
tool (`$B` newlines, `$N`/`$C`/`$R` substitutions, `$G` gender branches as
m-/f- file variants, angle-bracket stage directions stripped).

## Automation on the owner's machine (NixOS, systemd user units)

Installed by `tools/install-timer.sh`:

- `forever-vo-ingest.path` — fires on every write of the saved-variables
  file; runs `tools/ingest.sh` = pull, ingest, push `capture.json`.
- `forever-vo-daily.timer` — 04:00 nightly: sync, voice captured lines,
  work the bulk backlog for 2 h, rebuild tables, commit and push.
- `forever-vo-bulk.service` — the long bulk run, `Restart=on-failure` so a
  CUDA context lost to suspend just resumes (existing files are skipped).

Do not add a periodic pull timer; the owner declined it. Do not run two
generators at once by hand (they share `sound_index.json`).

## Releases

`.pkgmeta` at the root uses `move-folders` so only `ForeverVO/` ships. Push a
`v*` tag: the GitHub workflow builds a release with the BigWigs packager, and
CurseForge's own packager (repo linked as Source, tags only) publishes the
same zip. Do **not** set the `CF_API_KEY` secret, or files upload twice.
`CHANGELOG.md` is the release notes. Dry-run locally with the packager's
`release.sh -d -g 1.60.1`.

## Crowdsourcing

`/fvo export` packs a session's unvoiced lines (character name replaced by
`$n`) via `C_EncodingUtil` into an `FVO1:` string. Players paste it as a
comment on issue #1; `.github/workflows/ingest-captures.yml` decodes it with
`tools/exportfile.py` (stdlib only) into `captures/` and reacts with a rocket.
The owner's machine picks those up on the next sync.

## Gotchas already paid for

- A bash heredoc inside a Python heredoc terminates at the inner `EOF`. Use
  different terminators or the Write tool.
- `pkill -f 'tools/generate.py'` matches the shell that runs it; use
  `pgrep -f` to look and `systemctl --user stop forever-vo-bulk` to stop.
- The wago.tools CSV export is complete for client tables, but the beta's
  `BroadcastText` really is 12 rows; gossip is server-pushed on this engine.
- `questcache.wdb` records have a variable fixed part; `wdbcache.py` scans
  every offset and prefers the candidate with no objectives block. It parses
  all 258 records of the owner's cache and was validated against Classic
  titles.
- `PlayerModel:GetDisplayInfo()` returns 0 until the model loads; the capture
  reads it in `OnModelLoaded`, and the merge ignores zero display IDs.
- The Forever client picks `_Camelot.toc` when present and `.toc` otherwise;
  this addon uses the plain `.toc` with Interface 16001.

## Things the owner wants next

- Per-line configurability: let end users nudge text, voice, exaggeration or
  pacing for a line and re-run Chatterbox for it themselves.
- A Discord bot as an alternative inbox for `FVO1:` strings (same decoder).
- A first `ForeverVO_Data` release once the Classic bulk run finishes.
