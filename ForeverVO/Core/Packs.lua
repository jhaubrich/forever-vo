local _, ns = ...
local Util = ns.Util

--[[
Voice packs are separate addons that depend on ForeverVO and call
ForeverVO.RegisterPack(pack) with a table of this shape:

  {
    name = "Forever", version = "0.1", priority = 100,
    folder = "ForeverVO_Data",          -- Interface\AddOns\<folder>\Sounds\...
    quests = {
      [questID] = { a = 5.2, p = 2.0, c = 3.1, g = "a", npc = 288,
                    cP = { { d = 1.1 }, { d = 1.4, n = true }, { d = 0.6 } } },
      -- a/p/c: duration in seconds of accept/progress/complete audio (absent = no file)
      -- g: which of a/p/c exist as m-/f- variants, because $G branches per line
      --    ("a" = only the accept text branches). `true` means all of them, as
      --    packs built before this wrote it.
      -- npc: quest giver speaker key (creature ID, negative for game objects);
      --    absent when an item starts the quest. ender: the turn-in speaker,
      --    only when it differs from the giver. The addon uses them for a text
      --    the client leaves unattributed (an item-started or shared quest, a
      --    turn-in at a game object); packs built before ender existed carry
      --    the first speaker of any event as npc.
      -- aP/pP/cP: the parts of a line that mixes the speaker and the narrator,
      --    in reading order, each with its duration; n marks the narrator's
      --    (a <stage direction>). Files are <questID>-p<i>-<event>.mp3.
    },
    gossip = {
      [speakerKey] = {
        { f = "288-1a2b3c4d", h = "1a2b3c4d", t = "original text", d = 4.5, g = true,
          n = { [1] = 4.7 },      -- duration per alternate narrator voice
          P = { { d = 2.0 }, { d = 1.1, n = true } },   -- parts, as aP above; files <f>-p<i> with
          nP = { [1] = { [2] = 1.2 } } },               -- the speaker before the hash: 288-p2-1a2b3c4d
      },
    },
    npcs = { [speakerKey] = "Name" },
    narratorVoices = { "human-female", "dwarf-male" },
    narrator = {
      [questID] = { [1] = { a = 5.4, c = 3.3, cP = { [2] = 1.2 } }, [2] = { a = 5.1 } },
      -- the same quest read in each alternate narrator voice, indexed into
      -- narratorVoices, with that recording's own durations; aP/pP/cP hold the
      -- durations of the narrator's parts of a mixed line, by part index
    },
  }

Files live at Sounds\Quests\<questID>-accept.mp3 (with m-/f- prefix when g is
set) and Sounds\Gossip\<f>.mp3. Higher priority packs are consulted first, so a
pack of new or revised lines can sit on top of a base pack.

Lines with no speaker to clone - quests and gossip from objects and items - are
read by a narrator. Packs may carry those lines again in other voices, under
Sounds\<Quests|Gossip>\Narrator\<voice>\, and the player picks one
(ns.db.narratorVoice); a line the chosen voice has no recording for falls back
to the default narrator. Quest alternates are in the narrator table, gossip
alternates in each entry's n field, both indexed into narratorVoices.

A line that mixes the speaker and the narrator - "Hmm... <Jorgen looks up at
you.> All right." - has parts: the speaker's words in their voice and each
stage direction in the narrator's, played back to back by the queue, with the
narrator's parts swapped for the chosen voice where the pack carries them. The
whole-line file, with the stage directions left out, is still there for addons
that predate parts; a line that is only a stage direction has parts and no
whole-line file.
]]

local Packs = {
    list = {},
    byName = {},
}
ns.Packs = Packs

local FUZZY_THRESHOLD = 0.6
local QUEST_FIELD = { accept = "a", progress = "p", complete = "c" }

function ns.RegisterPack(pack)
    assert(type(pack) == "table" and pack.name and pack.folder, "ForeverVO.RegisterPack: pack needs name and folder")
    if Packs.byName[pack.name] then
        ns.Print(format("voice pack %q registered twice, ignoring the second copy", pack.name))
        return
    end
    pack.priority = pack.priority or 0
    pack.quests = pack.quests or {}
    pack.gossip = pack.gossip or {}
    pack.npcs = pack.npcs or {}
    pack.narrator = pack.narrator or {}
    pack.narratorVoices = pack.narratorVoices or {}
    Packs.voices = nil -- the menu is the union over packs; rebuild it on demand
    pack.nameToKey = {}
    for key, name in pairs(pack.npcs) do
        pack.nameToKey[name] = pack.nameToKey[name] or key
    end
    Packs.byName[pack.name] = pack
    table.insert(Packs.list, pack)
    table.sort(Packs.list, function(a, b)
        if a.priority ~= b.priority then
            return a.priority > b.priority
        end
        return a.name < b.name
    end)
    ns.Debug("registered pack", pack.name, pack.version or "")
    if ns.Queue then
        ns.Queue:TriggerEvent("OnPacksChanged")
    end
end

function Packs:Count()
    return #self.list
end

function Packs:Iterate()
    return ipairs(self.list)
end

local function SoundPath(pack, subfolder, base)
    return format("Interface\\AddOns\\%s\\Sounds\\%s\\%s.mp3", pack.folder, subfolder, base)
end

-- ---------------------------------------------------------------------------
-- Narrator voice
-- ---------------------------------------------------------------------------

local DEFAULT_NARRATOR = "narrator"
local NARRATOR_CVAR = "ForeverVO_narratorVoice"
local RACE_LABELS = {
    human = "Human", dwarf = "Dwarf", nightelf = "Night elf", orc = "Orc", troll = "Troll",
    tauren = "Tauren", gnome = "Gnome", goblin = "Goblin", bloodelf = "Blood elf",
    scourge = "Undead", skyborne = "Skyborne", draenei = "Draenei",
}

Packs.defaultNarrator = DEFAULT_NARRATOR

--- The voices the player may pick for narrated quests: the default first, then
--- every alternate the installed packs carry.
function Packs:NarratorVoices()
    if self.voices then
        return self.voices
    end
    local voices, seen = { DEFAULT_NARRATOR }, { [DEFAULT_NARRATOR] = true }
    for _, pack in ipairs(self.list) do
        for _, voice in ipairs(pack.narratorVoices) do
            if not seen[voice] then
                seen[voice] = true
                table.insert(voices, voice)
            end
        end
    end
    self.voices = voices
    return voices
end

--- "dwarf-male" -> "Dwarf male". Unknown races keep their own name, capitalised.
function Packs.NarratorVoiceLabel(voice)
    if voice == DEFAULT_NARRATOR then
        return "Narrator"
    end
    local race, gender = voice:match("^(.+)%-(%a+)$")
    if not race then
        return voice
    end
    return format("%s %s", RACE_LABELS[race] or (race:sub(1, 1):upper() .. race:sub(2)), gender)
end

function Packs:NarratorVoice()
    local voice = ns.db.narratorVoice or DEFAULT_NARRATOR
    for _, available in ipairs(self:NarratorVoices()) do
        if available == voice then
            return voice
        end
    end
    return DEFAULT_NARRATOR -- the pack that carried it is no longer installed
end

--- Picks the narrator voice, and remembers it in an addon CVar: this client
--- writes saved variables but never reads them back (see Welcome.lua).
function Packs:SetNarratorVoice(voice)
    ns.db.narratorVoice = voice
    pcall(C_CVar.SetCVar, NARRATOR_CVAR, voice)
end

ns.OnInit(function()
    pcall(C_CVar.RegisterCVar, NARRATOR_CVAR, DEFAULT_NARRATOR)
end)

ns.OnLogin(function()
    local stored = C_CVar.GetCVar(NARRATOR_CVAR)
    if stored and stored ~= "" then
        ns.db.narratorVoice = stored
    end
end)

--- Where a voice sits in this pack's narratorVoices, or nil when it has none.
local function NarratorIndex(pack, voice)
    for index, name in ipairs(pack.narratorVoices) do
        if name == voice then
            return index
        end
    end
end

--- The record a pack holds for a narrated quest in the chosen voice, or nil.
local function NarratorRecord(pack, questID, voice)
    local alternates = pack.narrator[questID]
    local index = alternates and NarratorIndex(pack, voice)
    return index and alternates[index] or nil
end

--- The files of a line that mixes the speaker and the narrator, in reading
--- order: { path, duration } per part, the narrator's parts in the chosen
--- narrator voice where `alternates` (part index -> duration) carries them.
--- Returns the list and the summed duration.
local function ResolveParts(pack, subfolder, base, parts, alternates, voice)
    local head, last = base:match("^(.*)%-([^%-]+)$")
    local resolved, total = {}, 0
    for index, part in ipairs(parts) do
        local name = format("%s-p%d-%s", head, index, last)
        local path, seconds = SoundPath(pack, subfolder, name), part.d or 0
        if part.n and alternates and alternates[index] then
            path, seconds = SoundPath(pack, subfolder .. "\\Narrator\\" .. voice, name), alternates[index]
        end
        resolved[index] = { path = path, duration = seconds }
        total = total + seconds
    end
    return resolved, total
end

--- Finds the audio for a quest event. Returns path, duration, pack, parts or
--- nil. `parts` is set for a line the queue plays as a sequence (see
--- ResolveParts); `path` then names the whole-line file where one exists, or
--- the first part, so the queue can still tell one line from another.
---@param questID number
---@param event "accept"|"progress"|"complete"
function Packs:FindQuest(questID, event)
    local field = QUEST_FIELD[event]
    if not questID or not field then
        return nil
    end
    for _, pack in ipairs(self.list) do
        local entry = pack.quests[questID]
        local parts = entry and entry[field .. "P"]
        if entry and (entry[field] or parts) then
            local base = format("%d-%s", questID, event)
            -- g lists the events whose text branches on $G: a quest can branch on
            -- its greeting and not on its turn-in, and prefixing an event that was
            -- written unprefixed asks for a file that does not exist. Packs built
            -- before this carry `true`, which still means every event.
            if entry.g == true or (type(entry.g) == "string" and strfind(entry.g, field, 1, true)) then
                base = Util.PlayerGenderPrefix() .. base
            end
            local voice = self:NarratorVoice()
            local alternate = voice ~= DEFAULT_NARRATOR and NarratorRecord(pack, questID, voice) or nil
            if parts then
                local resolved, total = ResolveParts(pack, "Quests", base, parts, alternate and alternate[field .. "P"], voice)
                local path = entry[field] and SoundPath(pack, "Quests", base) or resolved[1].path
                return path, total, pack, resolved
            end
            if alternate and alternate[field] then
                return SoundPath(pack, "Quests\\Narrator\\" .. voice, base), alternate[field], pack
            end
            return SoundPath(pack, "Quests", base), entry[field], pack
        end
    end
end

--- Speaker key recorded by any pack for the quest: the giver, or for a
--- progress or complete text the turn-in speaker where the pack records one.
function Packs:QuestGiver(questID, event)
    local turnIn = event == "progress" or event == "complete"
    for _, pack in ipairs(self.list) do
        local entry = pack.quests[questID]
        local key = entry and ((turnIn and entry.ender) or entry.npc)
        if key then
            return key
        end
    end
end

function Packs:SpeakerName(key)
    if not key then
        return nil
    end
    for _, pack in ipairs(self.list) do
        local name = pack.npcs[key]
        if name then
            return name
        end
    end
end

function Packs:SpeakerKeyByName(name)
    if not name then
        return nil
    end
    for _, pack in ipairs(self.list) do
        local key = pack.nameToKey[name]
        if key then
            return key
        end
    end
end

--- Finds gossip/greeting audio for a speaker. Exact hash match first, then the
--- most similar text above the fuzzy threshold. Returns path, duration, pack.
---@param speakerKey number
---@param text string
function Packs:FindGossip(speakerKey, text)
    if not speakerKey or not text then
        return nil
    end
    -- Pack text carries the placeholders; the client has already expanded them
    -- in what we were handed. Tokenise once so both the hash and the fuzzy
    -- word sets compare like with like whoever is reading.
    local tokenized = Util.Tokenize(text)
    local hash = Util.TextKey(tokenized)
    local bestEntry, bestPack, bestScore

    for _, pack in ipairs(self.list) do
        local entries = pack.gossip[speakerKey]
        if entries then
            for _, entry in ipairs(entries) do
                if entry.h == hash then
                    bestEntry, bestPack, bestScore = entry, pack, 1
                    break
                end
            end
            if bestScore == 1 then
                break
            end
            for _, entry in ipairs(entries) do
                local score = Util.Similarity(tokenized, entry.t or "")
                if score >= FUZZY_THRESHOLD and (not bestScore or score > bestScore) then
                    bestEntry, bestPack, bestScore = entry, pack, score
                end
            end
        end
    end

    if not bestEntry then
        return nil
    end
    local base = bestEntry.f
    if bestEntry.g then
        base = Util.PlayerGenderPrefix() .. base
    end
    ns.Debug(format("gossip match %.2f for %s", bestScore, base))
    local voice = self:NarratorVoice()
    local index = voice ~= DEFAULT_NARRATOR and NarratorIndex(bestPack, voice) or nil
    if bestEntry.P then
        local resolved, total = ResolveParts(bestPack, "Gossip", base, bestEntry.P,
            index and bestEntry.nP and bestEntry.nP[index], voice)
        local path = bestEntry.d and SoundPath(bestPack, "Gossip", base) or resolved[1].path
        return path, total, bestPack, resolved
    end
    if index and bestEntry.n then
        local seconds = bestEntry.n[index]
        if seconds then
            return SoundPath(bestPack, "Gossip\\Narrator\\" .. voice, base), seconds, bestPack
        end
    end
    return SoundPath(bestPack, "Gossip", base), bestEntry.d, bestPack
end
