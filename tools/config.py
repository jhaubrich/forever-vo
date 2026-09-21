"""Paths and constants shared by the Forever Voiceover tools."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
DATA_DIR = TOOLS_DIR / "data"
DB2_DIR = DATA_DIR / "db2"
VOICES_DIR = TOOLS_DIR / "voices"
CAPTURE_JSON = DATA_DIR / "capture.json"
SOUND_INDEX = DATA_DIR / "sound_index.json"

ADDON_NAME = "ForeverVO"
PACK_NAME = "ForeverVO_Data"
PACK_DIR = ROOT / PACK_NAME
PACK_DATA_DIR = PACK_DIR / "Data"
SOUNDS_DIR = PACK_DIR / "Sounds"

WOW_DIR = Path(os.environ.get(
    "WOW_DIR",
    Path.home() / "Faugus/battlenet/drive_c/Program Files (x86)/World of Warcraft",
))
BETA_DIR = WOW_DIR / "_classic_beta_"
BETA_BUILD = os.environ.get("WOW_BETA_BUILD", "1.60.1.69913")
WAGO_BASE = "https://wago.tools"

# ChrRaces IDs -> voice family (forever-vo/tools/voices/<race>-<gender>.wav)
RACE_DICT = {
    -1: "narrator",
    1: "human", 2: "orc", 3: "dwarf", 4: "nightelf", 5: "scourge", 6: "tauren",
    7: "gnome", 8: "troll", 9: "goblin", 10: "bloodelf", 11: "draenei",
    12: "felorc", 13: "naga", 14: "broken", 15: "skeleton", 16: "vrykul",
    17: "tuskarr", 18: "foresttroll", 19: "taunka", 20: "northrendskeleton",
    21: "icetroll", 22: "worgen", 23: "human", 24: "pandaren", 25: "pandaren",
    26: "pandaren", 27: "nightborne", 28: "highmountaintauren", 29: "voidelf",
    30: "lightforgeddraenei", 31: "zandalari", 32: "kultiran", 33: "thinhuman",
    34: "darkirondwarf", 35: "vulpera", 36: "magharorc", 37: "mechagnome",
    52: "dracthyr", 70: "dracthyr",
    95: "skyborne", 96: "skyborne",
}
GENDER_DICT = {0: "male", 1: "female"}

# Voices without a reference clip borrow a related one; "narrator" is used for
# quests given by items or objects. Provide tools/voices/narrator.wav to override.
FALLBACK_VOICES = {
    "narrator": "human-male",
    "felorc": "orc", "magharorc": "orc",
    "foresttroll": "troll", "icetroll": "troll", "zandalari": "troll",
    "skeleton": "scourge", "northrendskeleton": "scourge",
    "taunka": "tauren", "highmountaintauren": "tauren",
    "draenei": "human", "broken": "human", "vrykul": "human", "kultiran": "human", "thinhuman": "human", "worgen": "human",
    "naga": "nightelf", "nightborne": "nightelf", "voidelf": "bloodelf",
    "tuskarr": "dwarf", "darkirondwarf": "dwarf", "mechagnome": "gnome", "vulpera": "goblin",
    "pandaren": "human", "dracthyr": "bloodelf",
}

# Quests handed out by objects and items have no speaker to clone, so they are
# read by the narrator. That voice is a matter of taste, so every narrator line
# is also generated in the alternates below and the player picks one in the
# addon's options. NARRATOR_VOICE is the default and keeps the plain sound path
# (Sounds\Quests\<base>.mp3); the alternates live in Sounds\Quests\Narrator\<voice>\.
# Adding a voice here costs one more pass over every narrator line.
NARRATOR_VOICE = "narrator"
NARRATOR_VOICES = [
    NARRATOR_VOICE,
    "human-female",
    "dwarf-male",
    "nightelf-female",
    "orc-male",
    "troll-female",
]

# When the client tables give no race for a speaker, the zone is a strong hint.
ZONE_RACE_HINTS = {
    "Zephras Isle": "skyborne",
}

# CurseForge project IDs. The player addon's ID also lives in its TOC. The
# API key stays out of git (.env: CF_API_KEY, or CURSEFORGE_API_KEY).
CURSEFORGE_PROJECTS = {
    "addon": 1705010,   # Forever Voiceover
    "delta": 1705094,   # Forever Voiceover Data: Forever
    "base": 1705100,    # Forever Voiceover Data: Base (Classic set)
}

# The client resolves $n, $c and $r against whoever is reading before any addon
# can see the text, so a line first seen on a rogue is captured saying "rogue".
# Capture version 3 records the reader's class and race so ingest.py can put the
# placeholders back; these are the characters whose captures predate it. Derived
# by aligning the captured text against the raw $c still held in
# tools/data/bulk/questcache.json (and confirmed by the owner for Agravain).
LEGACY_CHARACTERS = {
    "Myrlin Fixpoint": {"class": "Mage"},
    "Pellinore Fixpoint": {"class": "Hunter"},
    "Agravain Fixpoint": {"class": "Rogue"},
}
