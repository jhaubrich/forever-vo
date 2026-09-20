# Changelog

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
