# CLAUDE.md — Forever Voiceover

Notes for agents working in this repo. Read this before touching anything.

## What this is

Voiced quests and NPC dialogue for **World of Warcraft: Forever** (the
Classic-plus client, codename Camelot). Two addons plus a Python pipeline:

- `ForeverVO/` — the player addon. Lua, Retail-engine APIs only, no libraries.
- `ForeverVO_Data/` — the owner's working voice pack (generated Lua tables +
  mp3s). Only the tables are in git; the audio lives on the owner's machine and
  ships as three CurseForge packs (Releases).
- `tools/` — capture ingestion, text sources, voice reference building,
  Chatterbox TTS generation, pack tables, release packaging.
- `captures/` — community `/fvo export` submissions, committed by a GitHub
  Action (Crowdsourcing).

Owner: Quinn Dougherty (quinn@for-all.dev). Addon slug `forever-vo`, display
name "Forever Voiceover". CurseForge project IDs are under
`[release.curseforge_projects]` in `forever-vo.toml`. License MIT; voice packs
are non-commercial fan content (Blizzard's text, voices cloned from the game's
own recordings).

## The client, and why so much is unusual

- Product `wow_classic_beta`, version 1.60.1, **Interface 16001**. It runs the
  **Retail engine** (`WOW_PROJECT_ID == WOW_PROJECT_MAINLINE`) with Classic
  data: port from Retail code paths, never Classic ones. The client picks
  `_Camelot.toc` when present, else `.toc`; this addon uses the plain `.toc`.
- Blizzard's UI source for this build: Gethe's `wow-ui-source`, branch
  `forever`, sparse checkout at `~/Projects/wow-ui-source` (Camelot overrides in
  `*/Camelot/`). Check there before assuming an API or frame exists.
- `docs/forever_api.json` lists the client's globals, frames and `C_*`
  namespaces; `./tools/run.sh tools/apicheck.py` diffs the addon against it.
  Run it after any Lua change.
- Removed globals that bite: `MouseIsOver` (use `frame:IsMouseOver()`),
  `SetDesaturation` (`texture:SetDesaturated()`), `InterfaceOptions_AddCategory`
  (Settings API), `GetGossipText` (`C_GossipInfo.GetText()`).
  `GameTooltip:SetText` rejects the old 6-argument form.
- **Unit identity can be secret.** `UnitName`, `UnitGUID`, `UnitSex`,
  `UnitRace`, `UnitClass` return a *secret value* while a unit's identity is
  restricted; it displays through `SetText` but errors in any string op,
  comparison or table key. Every read of a unit's identity or of dialog text
  goes through `Util.Plain`, which turns a secret into nil. Keep new reads
  behind it.
- **Saved variables persist** (since about 2026-09-24; before that the beta
  never read them back). **Addon-registered CVars do not**: they survive a
  `/reload` and are gone at the next login. Never restore a setting from one;
  the narrator pick and the developer overlay did and reset at every login
  (#887). The welcome window's `ForeverVO_welcomed` CVar is harmless (only ORed
  in). Ingest treats every write of the saved-variables file as a merge, and
  must: the DB accumulates across sessions and characters.
- New files under `AddOns` need a client restart, not a `/reload`, before
  `PlaySoundFile` finds them.
- The addon compartment does not show on the Camelot minimap skin, hence our
  own minimap button.
- Quest and gossip **text is not in the client files**; the server sends it
  (wago.tools' `QuestV2`/`BroadcastText` carry none). Text comes only from
  in-game capture and the VMaNGOS Classic snapshot. The client's
  `questcache.wdb` was dropped as a source (#317); do not bring it back.

## Addon conventions

- Every file starts `local _, ns = ...`; only `ForeverVO` (the namespace) and
  the three compartment handlers are globals. Modules register with
  `ns.OnInit`/`ns.OnLogin`.
- UI follows Blizzard's own frames: the talking head rebuilds
  `TalkingHeadFrame`; buttons use `UIPanelButtonTemplate`; options use the
  Settings API; the queue is a `CallbackRegistryMixin`; rows use frame pools.
  No embedded libraries, native look. The talking head has two kits (faction
  parchment, Blizzard's dark panel), each with its own text colours
  (`FONT_COLORS` in `UI/TalkingHead.lua`); check both after touching it.
- The login window (`Core/Welcome.lua`) is a plain frame, not a StaticPopup:
  StaticPopups from addons taint the gamepad UI path and froze the client (#44).
  It shows once per run of the client (`welcomeShownAt`, the client's start
  time from `time() - GetSessionTime()`).
- Voice pack format is documented at the top of `ForeverVO/Core/Packs.lua`.
  Quests are keyed by ID and event; gossip by speaker key (creature ID,
  negative for game objects) plus a text hash, with a Jaccard fuzzy fallback.
- **Books** (since 2026-10-05): readable text (books in the bags, plaques and
  lecterns in the world, all `ItemTextFrame`) is a third kind. A page is keyed
  by `Util.TextKey` of its text with no speaker (`ItemTextGetItem` gives a name,
  not an ID, and the `npc` unit is stale), found by `Packs:FindBook` (exact
  hash, else Jaccard over the same title's pages), captured in `db.books`
  (capture version 7) and exported as `k = "book"`; the narrator reads every
  page in every narrator voice. Nothing plays on opening: a Play button under
  the book (`UI/Book.lua`, the owner's call) reads the page on screen and every
  voiced page after it. The client hands an addon only the page on screen, so
  each page record carries `p` (its number), `x` (the next voiced page's key)
  and `s` (the prose the talking head shows; `t` stays the raw page FindBook
  matches). `generate.link_pages` takes Classic's `next`, and lets a captured
  page replace that next only when the capture rewords it or is the only page
  of that number: one reading of a shared title must not chain every book of
  that name. A page turn or closing the book never stops a reading; after Play
  a page turned to beyond the chain queues too; Stop clears every page this
  button queued, including ones already past the page on screen, and Play again
  starts a fresh one. Pages are captured only as viewed: the
  addon does not turn pages itself, and the client's `pagetextcache.wdb` is out
  for the same reason as the quest cache (#317).
- `luac -p` every changed Lua file (`./tools/run.sh luac -p <file>`). There is
  no in-game test harness; the owner tests by `/reload`.

## Text, placeholders and capture repair

- **The text hash must stay identical in Lua and Python**:
  `Util.Tokenize`/`NormalizeText`/`HashText`/`ResolveGender` in `Core/Util.lua`
  and `tools/textkey.py`/`textclean.split_gender`. Touch one, touch the other,
  and run `./tools/run.sh tools/textkey_parity.py` (real captures, Classic's raw
  text when `bulk/classic.json` exists, edge cases).
- **The client expands `$n`, `$c`, `$r` before any addon sees the text.**
  `Util.Tokenize` puts them back at capture (whole words only; the name
  case-sensitive, class and race case-folded, ASCII only on both sides), and
  `NormalizeText` drops placeholders so one recording matches every reader. A
  multi-word race renders `$r` as its last word ("skyborne"); both tokenizers
  match it, but ingest applies that only to captures from 0.1.5 on
  (`SHORT_RACE_SINCE`), since Forever's own text says "skyborne" literally.
- **Ingest repairs captures in one idempotent pass** (`repair_entry`): re-tokenise,
  un-glue placeholders inside words (old addons wrote `w$nh`), and reconcile
  against Classic's raw text (alignment ≥ 0.9) in both directions. Community
  exports from before 0.1.7 carry no class or race; `[readers.community]` and
  `[readers.legacy]` in the TOML say who read them.
- **A second reader settles `$c`/`$r` on Forever's own lines** (no raw text):
  `merge_entry` records `settled` where two trusted readings disagree, and
  `tokenize_entry` leaves a settled trait alone.
- **The client resolves `$g male:female;` too.** Captures record the reader's
  sex; ingest restores the branch from raw text (`restore_gender`) or rebuilds it
  from a male and a female reading (`merge_gender`/`rebuild_gender`). Gossip
  stays resolved (keyed by live text), and the addon also matches a branching
  pack entry resolved for the player's sex (`GossipExact`, #365).
  `./tools/run.sh tools/gender_check.py` after touching any of this.
- **Self-repair is a development principle**: every release has recorded
  something wrong that a later one fixes, and the fix must reach lines already
  captured through ongoing play, never by hand. When designing any capture
  change, ask how a line the previous version captured gets replaced. The
  machinery:
  - Captures carry the addon version; `merge_entry` ranks readings by
    `capture_rank` (newer addon, then later reading).
  - `needs_of` decides which readers a quest line still wants (`needs`), written
    to the pack as `wa`/`wp`/`wc`; the addon then captures and exports the line
    although it is voiced (`wanted`). That is the general hook: make `needs_of`
    ask and players re-supply the line. No hand-built exports.
  - `trusted_since` under `[readers]` re-asks everything before a release when
    its flaw touched everything; for a narrower flaw add a `(fixed_in,
    predicate)` to `KNOWN_FLAWS` in `ingest.py` instead, so only matching lines
    are re-asked. `superseded_gossip` drops an untrusted gossip line once a
    trusted one from the same speaker aligns.
  - Each quest record carries `ha`/`hp`/`hc`, the key of the text it was voiced
    from; `Packs:QuestTextMatches` flags live text that differs (`differs`), and
    the capture is exported and wins at ingest.
  - At login `Capture:Refresh` re-checks waiting lines against the installed
    packs, so a line a newer pack voiced stops counting as "to send".
  Checked by `gender_check.py` and `tests/test_ingest.py`.

## Pipeline (tools/)

`tools/` is the Python package of the root `pyproject.toml` (flat, imports as
`from tools.config import ...`). Pinned three ways: `.python-version` +
`uv.lock` for Python, `flake.nix`/`flake.lock` for the rest (uv, `ffmpeg-full`
for rubberband, lua 5.1, CUDA libraries). **Python is only ever invoked through
uv**; `tools/run.sh` is `nix develop -c uv run "$@"`, so `./tools/run.sh
tools/<x>.py`, `./tools/run.sh fvo-<x>`, `./tools/run.sh python -c ...` and
`./tools/run.sh luac -p ...` all use the same pins (`tools.*` imports work from
anywhere in the repo). The GPU stack is the `tts` dependency group, on by
default; `--no-group tts` skips torch. `tts-rocm` is the same Chatterbox on
ROCm for audition only (README; `fvo-generate` refuses it). Bump a pin with `uv
lock --upgrade-package <name>` or `nix flake update` and commit the lock.

**Configuration is `forever-vo.toml`**, validated by the pydantic models in
`tools/config.py` (an unknown key is an error). If someone edits it, it is TOML;
if nobody does (paths, race IDs), it stays a Python constant. There is
deliberately no per-line table: the file would grow without bound. Functions
take the section they need by type hint; `textclean.clean()` and
`wowdata.voice_for_npc()` default to the repository's file.

**Checks**: `./tools/run.sh pytest`, `ruff check`, `ruff format --check` (ruff's
defaults, no `[tool.ruff]`), `ty check`, all clean. `check.yml` runs them plus
`luac5.1 -p`, `apicheck` and `textkey_parity` on pull requests (not on pushes to
main, which are mostly captures and tables). A test must not depend on
gitignored files (voice clips, `bulk/classic.json`).

Data flow (JSON is the source of truth; `ForeverVO_Data/Data/*.lua` is a build
artifact, never hand-edited):

1. `ingest.py` merges `WTF/Account/*/SavedVariables/ForeverVO.lua` and
   `captures/*.json` into `tools/data/capture.json`.
2. `classicdb.py` exports the VMaNGOS snapshot to `tools/data/bulk/classic.json`
   (gitignored, regenerable), including every creature's display and every
   book page (`books`, version 4: `page_text` chains from items, type-9 text
   objects and type-10 objects, 1,124 pages keyed by text, with each page's
   `next`). Capture beats Classic.
3. `generate.py` picks a voice per speaker (below), synthesises with Chatterbox,
   writes mp3s under `ForeverVO_Data/Sounds/`, rebuilds the tables every 25
   files, and records per file in `sound_index.json` the voice (`v`) and a
   fingerprint (`t`: spoken text, plus tuning knobs and picks digest that differ
   from the defaults). A file is regenerated when either changes. Before
   generating it rebuilds any picked reference whose wav was not built from its
   current pick (`ensure_picked_references`, Voices).
4. `build_voice_references.py` builds cloning clips under `tools/voices/` from
   the client's own audio via wago.tools (Voices).

Generation details:

- Text cleaning: `$B` newlines, `$N`/`$C`/`$R` substitutions, `$G` branches as
  m-/f- file variants, `[pronunciations]` respellings (whole words, any case).
- Angle-bracket stage directions are the narrator's: the line also gets *parts*,
  `<questID>-p<i>-<event>` / `<speaker>-p<i>-<hash>`, played back to back
  (`aP`/`pP`/`cP`, gossip `P`/`nP`). The part number sits before the last name
  segment on purpose.
- Sound names: quests `<questID>-<event>`, gossip `<speaker>-<hash>`. Tell them
  apart by the last segment (`generate.sound_folder`), not by whether the first
  is numeric.
- Narrator lines (objects, items, genderless speakers) are made once per voice
  in `[voices]`: `narrator` (human-male's clip) at the plain path, each of
  `narrator_alternates` under `Sounds/<Quests|Gossip>/Narrator/<voice>/` (index
  key `Narrator/<voice>/<base>`, `Data/Narrator.lua`, gossip `n`). Alternates
  sort last in the to-do list. Releases only include configured voices; dropped
  voices' files stay on disk unused.
- A speaker met as both sexes (`sexes: "mf"`, one creature ID for male and female
  guards, #304) gets the whole line in the other sex too under
  `Sounds/*/Sex/<m|f>/` (tables `sa`/`sp`/`sc`, gossip `s`); the addon plays it
  when the dialog unit's sex matches.

**Audition** (`uv run audition`, `tools/audition/`, FastAPI + one `index.html`,
port 8765) is the ear-test page: pick a voice and a line, source clips, knobs
and takes. It writes picks (`[voices.sources]`), tuning (`[tts.voices]`),
pronunciations, speaker pins, approvals (`[voices.approved]`, ★ while the
recipe heard is current) and tasting notes (`[voices.notes]`) into the TOML
through tomlkit, validated before the file is replaced. "Write to pack"
regenerates one file under the *saved* configuration only. Takes go to
`tools/data/audition/` (gitignored).

## Automation on the owner's machine (NixOS, systemd user units)

Installed by `tools/install-timer.sh`:

- `forever-vo-daily.timer` — 02:30 nightly, done by 07:00: sync and ingest
  (`tools/ingest.sh`), voice captured lines, work the backlog until 90 min
  before 07:00, rebuild tables, upload Books, Base Endgame and the delta when
  due, commit and push. It stops the
  bulk service for the duration and restarts it from an `EXIT` trap, also when a
  final `--dry-run` still counts files.
- `forever-vo-bulk.service` — `tools/bulk.sh`, the long resumable run:
  `FOREVER_VO_WORKERS` (drop-in `workers.conf`; 1 so it can share the GPU with
  the game, 2 for a big run with the game closed: 2.59x realtime against 1.68x)
  `generate.py --shard i/N` processes. `Restart=on-failure`,
  `WantedBy=default.target`, holds an idle inhibitor.
- `forever-vo-watchdog.timer` (not in the repo: `~/.local/bin/forever-vo-watchdog.sh`)
  notifies on failure, stall, completion and low disk every 10 minutes.

Do not add a periodic pull timer; the owner declined it. Parallel *shards* are
fine: `save_sound_index` merges only the keys a process wrote (`dirty`) under
an `flock`, never over a higher `index_rank`, and every table, index and mp3 is
written to a temporary file and renamed. Two *unsharded* generators are wrong:
they walk the same to-do list. A one-off `generate.py --quest <id>` beside the
bulk run is fine, GPU memory permitting (the game takes ~4 GB, a worker ~7 GB).

## Releases

- **Addon**: push a `v*` tag; the GitHub workflow builds with the BigWigs
  packager, publishes a GitHub Release and uploads to CurseForge with the
  `CF_API_KEY` GitHub secret. `.pkgmeta` ships only `ForeverVO/`; anything at
  the root not in its ignore list ships as a stray folder, so add new root
  files there. `CHANGELOG.md` is the release notes; the owner usually pushes
  the tag. The packager's local dry run hangs walking `ForeverVO_Data/Sounds/`;
  CI has no audio and is fine.
- **Delta pack** "Forever Voiceover Data: Forever", priority 200: every line
  whose files differ from what the Base packs last shipped (stamps in the
  gitignored `tools/data/release_baseline.json`). `release_pack.py delta
  --upload --if-changed` runs nightly. Past `delta_cap_mb` (400) the nightly
  stops uploading and says a Base release is due.
- **Base packs** "Data: Base" (quests to `base_split_level` 40, all gossip) and
  "Data: Base Endgame" (quests from 41), priority 100, each with its narrator
  alternates. Each build re-encodes its whole set from a freshly wiped staging
  folder (~45 min). Base Endgame (under 400 MB) goes nightly once 20 files are
  new (`--min-new 20`, no age fallback). Base is released by hand when the
  delta grows large (`release_pack.py base --upload`). **The website caps a
  file at 1 GB**, and the API refuses far less, which is why Base is split.
- **Books** "Data: Books" (`ForeverVO_Data_Books`, `books`, priority 100,
  ~230 MB with one alternate narrator): every book page, Classic's and
  Forever's alike, kept out of Base for the 1 GB cap. Nightly once 10 files are
  new. A pack with no files yet is never released. Until its baseline is
  recorded, only pages read in game go in the delta, or the whole Classic set
  would flood it.
- **Every pack tries the API.** One it refuses prints the website form fields
  ("release" file for 1.60.1) and the nightly raises a desktop notification,
  but the build is recorded all the same, as every build is: upload what you
  build, soon, since the delta already leaves its lines to that pack. Books and
  Base Endgame go before the delta in the nightly: building one records its
  baseline, which takes its lines out of the delta.

`release_pack.py` builds from the working folder `ForeverVO_Data` (never
shipped), re-encodes to mono 32 kbps mp3 at 22.05 kHz **with no Xing/Info
header** (`-write_xing 0`: the client misreads LAME's CBR `Info` frame and cut
every released line short until 2026-09-25), writes a manifest per pack, and
uploads via `wow.curseforge.com/api` (the public `curseforge.com/api/v1` returns
HTML to scripts). Versions are dates (`2026.09.20`, `.2` the same day), tracked
in `tools/data/release_state.json`. The local API key is `CF_API_KEY` in the
gitignored `.env`. CurseForge moderation holds new projects and first files for
a day; the storefront logo must be original art (`docs/logo.png`).

## Crowdsourcing

`/fvo export` packs unvoiced lines, plus voiced lines the pack asked for
(`wanted`, `differs`) and NPC records to recast, into an `FVO1:` string
(`C_EncodingUtil`; name replaced by `$n`, class and race per line). Long exports
split into parts of about 25 lines, one GitHub issue each via Copy Link, or one
pasted string via All at Once; lines count as sent only once their part's link
is copied (`exportedAt`). Players file the "Contribute captured lines" issue
form (label `capture`); the older inbox, comments on pinned issue #1 (label
`capture-inbox`), still works. `.github/workflows/ingest-captures.yml` decodes
with `tools/exportfile.py` (stdlib only) into `captures/`, commits, reacts and
closes the issue. `exportfile.py` routes a kind it does not know to gossip, so a
new export kind (`book`) must reach main before an addon that writes it ships. It runs on opened, edited and on the `capture` label being
added, with no concurrency group (GitHub cancels queued runs in a group, and a
multi-part export opens its issues seconds apart); the push retries.

## Voices

**Accent and delivery come from the source clips in the reference audio.
`exaggeration` and `cfg_weight` are for colour and variety.** This is settled;
do not propose the knobs as the fix for a wrong accent, a flat delivery or a
voice that sounds like the wrong race. The evidence: goblin-male's accent did
not move across four settings pairs on two references, while changing the
clips took it from "bad" to level with the shipped pack; fifteen head clips
gave British, Southern and General American in turn, stably across takes.
Some regional accents do not survive cloning at all. Order of attack for a
voice that sounds wrong: the clips in the first 6 s, then the rest of the 10 s
window, then the knobs. **Only the first 6 s (t3) and 10 s (s3gen) of a
reference reach the model.**

How a speaker gets a voice (`wowdata.voice_for_npc`), first match wins:

1. `[voices.speakers]` pins (creature ID, negative for objects), for a speaker
   the data cannot fix; `generate.py` warns about a pinned voice with no clip.
2. `npc-<displayID>.wav`, a named NPC's own clip.
3. The display's race and sex (`CreatureDisplayInfoExtra`), cast as the
   archetype for its NPCSounds set where one is built (or a sibling set's,
   `wowdata.sibling_sets`: the same actor filed under several set IDs).
4. Without a display (Forever-only NPCs; the Forever client never yields one),
   race and sex from the captured `modelFileID` via `CreatureModelData`, majority
   over its display rows. A legacy player-race model reads as its HD twin
   (`tools/data/character_models.json`): the client reports either, by reader,
   and the legacy file has no display rows (#810). Classic NPCs get Classic's
   display (`generate.fill_displays`) when the model matches.
5. A species clip by model file (`tools/data/species_models.json`,
   `[voices.species_aliases]`), from Warcraft III unit sounds or retail.
6. `[voices.sound_sets]` by NPCSounds set, then `[voices.zone_hints]`, then human.
   Genderless speakers, objects and items go to the narrator.

`[voices.fallbacks]` sends races without a clip to a close one.
`generate.VoiceCatalog` resolves a voice to the clip it actually uses and takes
*that* voice's tuning; an archetype without its own `[tts.voices]` row takes
its race's knobs.

Captured models: the NPC record keeps one reading per source in `modelReads`
(from `MODEL_TRUSTED_SINCE` on; before 0.1.7 a shared model frame could record
the previous NPC's model, #352) and takes the majority. The pack records the
model it cast from (`pack.models`); a player who sees another marks the NPC
`recast` and the export carries it. `sexes` unions every sex met.

Clips:

- **Names.** `<race>-<gender>.wav` for a race's main voice; an archetype adds
  the word Blizzard's sound folder uses (`dwarf-male-guard`,
  `human-female-official`), or `-s<NPCSounds row>` when there is none.
  `wowdata.base_voice`/`is_archetype` are the only things that parse names. The
  builder's stale sweep deletes three-segment wavs it did not mint.
- **Automatic recipes** (`recipe_for`): a head of the race's spoken emote lines
  (JOKE/FLIRT; each voice a different slice), then the set's own greetings.
  Races with no spoken emotes in this client (blood elf, goblin) borrow
  retail's (`RETAIL_BUILD`): their plain voice is `pooled-barks`, their
  archetypes `speech-and-barks`/`speech-only`. Skyborne use `VocalUISounds`.
- **Clips chosen by ear beat every rule.** `[voices.sources.<voice>]` lists
  FileDataIDs, head first, optional `gaps` (silence after each clip, ≤ 3 s, keep
  it a breath inside the 10 s window). `build_picked_reference` concatenates
  exactly those, no sorting or filtering. Pick in audition's Source clips panel
  or `fvo-refclips`. The picks join the fingerprint, so a re-pick restages its
  voice; the wav's bytes do not.
- **The wavs are not in git; the picks are.** Each picked wav has
  `<voice>.picks.json` beside it, and `generate.py` rebuilds any picked wav
  whose record is missing or disagrees before generating (#890). Without that, a
  re-pick merged from another machine restaged its voice from the old wav and
  stamped the files current (2,042 files, 2026-10-03). An audition experiment
  that was never kept is put back the same way.
- **Named NPCs** (`npc-<displayID>`, kits used by ≤ 3 models): audition offers
  the kit plus every file in the set's sound folder (`fvo-soundpaths --folders`,
  committed to `tools/data/named_folders.json`; extra folders via
  `[voices.named_folders]` and `[voices] clip_folders`, never by name matching).
  Test a listed file for audio by content (`wowdata.is_dud`), never by length.
  Downloads are cached once per build in `tools/data/casc/<build>/`.
- **Species clips**: Warcraft III units via CascLib (`extract_wc3_units.py`,
  `build_wc3_references.py`), children from retail (`build_retail_references.py`).

Tuning, `[tts]` with `[tts.voices.<voice>]` overrides (may name another clip as
`reference`, which means its picks are never read): `exaggeration` and
`cfg_weight` are model knobs; `speed` (varispeed), `tempo` (`atempo`) and
`pitch` (`rubberband`, semitones) are ffmpeg filters after the model
(`generate.encode_filters`), checked before the model loads
(`require_filters`). Chatterbox pulls clones toward its own mid-range:
Varimathras needed -3 to -5 semitones. Only knobs that differ from the defaults
join the fingerprint, so tuning one voice restages exactly its files. To redo a
voice after rebuilding its clip by hand: `generate.py --force --voice <voice>`.

## Gotchas already paid for

- A bash heredoc inside a Python heredoc terminates at the inner `EOF`.
- `pkill -f 'tools/generate.py'` (and `pgrep -af`) match the shell running the
  command; filter on `.venv/bin/python tools/generate.py` and stop with
  `systemctl --user stop forever-vo-bulk`.
- A suspend can kill the GPU until reboot (`journalctl -k | grep Xid`;
  `nvidia-smi` still answers). `generate.py` refuses the CPU without `--cpu`.
  GNOME suspends on its own idle timer regardless of logind inhibitors, so
  `sleep-inactive-ac-type` is set to `'nothing'`; if a run dies at a round
  two-hour mark, check that first.
- After a search-and-replace edit, grep for the new text; a patch whose anchor
  had drifted once silently never applied.
- An index entry with `v: null` and no `t` is a probed placeholder, blind to the
  voice and text checks; `generate.py --reindex` restamps `t`.
- Anything that walks sounds by `glob("*/*.mp3")` misses the `Narrator/` and
  `Sex/` folders by design; they have their own scans.
- The wago.tools export is complete, but the beta's `BroadcastText` really is
  12 rows; gossip is server-pushed.
- 38 of Classic's book pages are SimpleHTML (`<HTML><BODY><H1>...`):
  `textclean.book_text` reads them as prose (a heading or blank line ends a
  sentence, tags and pictures go) and a picture-only page gets no file; the
  addon does not capture one either.
- `Audio.Exists` tests a file by playing it, and checks made in the frame the
  first line starts (which also mutes the Dialog channel) failed in game, so
  only a book's first page was queued; `Queue:AddMany` checks every item
  before starting the first.
- Chatterbox occasionally repeats a word or drifts voice mid-take (#805, #883);
  a fresh take (`generate.py --force --quest <id>`) fixes it.

## Things the owner wants next

- Per-line configurability for end users without the repo (audition covers the
  owner's side; not yet parts or narrator alternates).
- A Discord bot as an alternative inbox for `FVO1:` strings (same decoder).
