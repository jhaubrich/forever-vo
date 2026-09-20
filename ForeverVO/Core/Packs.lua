local _, ns = ...
local Util = ns.Util

--[[
Voice packs are separate addons that depend on ForeverVO and call
ForeverVO.RegisterPack(pack) with a table of this shape:

  {
    name = "Forever", version = "0.1", priority = 100,
    folder = "ForeverVO_Data",          -- Interface\AddOns\<folder>\Sounds\...
    quests = {
      [questID] = { a = 5.2, p = 2.0, c = 3.1, g = true, npc = 288 },
      -- a/p/c: duration in seconds of accept/progress/complete audio (absent = no file)
      -- g: files exist as m-/f- variants for the player's gender
      -- npc: quest giver speaker key (creature ID, negative for game objects)
    },
    gossip = {
      [speakerKey] = {
        { f = "288-1a2b3c4d", h = "1a2b3c4d", t = "original text", d = 4.5, g = true },
      },
    },
    npcs = { [speakerKey] = "Name" },
  }

Files live at Sounds\Quests\<questID>-accept.mp3 (with m-/f- prefix when g is
set) and Sounds\Gossip\<f>.mp3. Higher priority packs are consulted first, so a
pack of new or revised lines can sit on top of a base pack.
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

--- Finds the audio for a quest event. Returns path, duration, pack or nil.
---@param questID number
---@param event "accept"|"progress"|"complete"
function Packs:FindQuest(questID, event)
    local field = QUEST_FIELD[event]
    if not questID or not field then
        return nil
    end
    for _, pack in ipairs(self.list) do
        local entry = pack.quests[questID]
        if entry and entry[field] then
            local base = format("%d-%s", questID, event)
            if entry.g then
                base = Util.PlayerGenderPrefix() .. base
            end
            return SoundPath(pack, "Quests", base), entry[field], pack
        end
    end
end

--- Quest giver speaker key recorded by any pack for the quest.
function Packs:QuestGiver(questID)
    for _, pack in ipairs(self.list) do
        local entry = pack.quests[questID]
        if entry and entry.npc then
            return entry.npc
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
    local playerName = UnitName("player")
    local hash = Util.TextKey(text, playerName)
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
                local score = Util.Similarity(text, entry.t or "")
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
    return SoundPath(bestPack, "Gossip", base), bestEntry.d, bestPack
end
