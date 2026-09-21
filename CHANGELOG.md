# Changelog

## Unreleased

- The narrator's voice is now yours to pick. Quests and gossip from objects and
  items have no speaker, so a narrator reads them; voice packs can carry those
  lines in several voices, and Options > Audio > Narrator voice chooses one
  (`/fvo narrator` cycles). Lines the chosen voice has no recording for keep
  the default narrator.
- The talking head now uses your faction's parchment by default; clear
  Options > Talking head > Faction parchment style for the dark panel.
- Fixed the queue panel's "nothing else is waiting to play" line hanging off
  the left edge of the panel.

## 0.1.0

First release: the player addon, without a voice pack. This version collects
the lines players see so the pack can be generated from them.

- Talking head styled after the client's own, with the speaker's model, name,
  quest title and the text paged in time with the audio.
- Queue with pause, skip, clear and reorder; a queue panel; play buttons in the
  quest log list and next to Back in the quest details.
- Options under Escape > Options > AddOns, an addon compartment entry with a
  playback menu, `/fvo` commands.
- Capture of every quest and gossip line seen, `/fvo export` to contribute
  them, a welcome note and chat reminders while no pack is installed.
- Voice packs register through `ForeverVO.RegisterPack`; quests are keyed by
  ID and gossip by speaker and text hash, with a fuzzy fallback.
