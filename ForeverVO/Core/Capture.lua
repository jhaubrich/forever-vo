local _, ns = ...
local Util = ns.Util

--[[
Records every quest and gossip line the player sees into the account-wide saved
variable ForeverVOCaptureDB, along with who said it and whether a pack had
audio for it. tools/ingest.py merges these files; tools/generate.py voices what
is missing. Nothing here affects playback.

Note: the Forever beta client does not read saved variables back at login, so
each session starts empty. The file is still written on logout.
]]

local Capture = {}
ns.Capture = Capture

local modelFrame

local function GetDB()
    local db = ForeverVOCaptureDB
    if type(db) ~= "table" then
        db = {}
        ForeverVOCaptureDB = db
    end
    db.version = 2
    db.quests = db.quests or {}
    db.gossip = db.gossip or {}
    db.npcs = db.npcs or {}
    return db
end

--- Reads the creature display ID through a hidden PlayerModel; the pipeline maps
--- it to a race and gender to choose a voice.
local function DisplayInfo(unit)
    local displayID, fileID
    pcall(function()
        modelFrame = modelFrame or CreateFrame("PlayerModel")
        modelFrame:SetUnit(unit)
        if modelFrame.GetDisplayInfo then
            displayID = modelFrame:GetDisplayInfo()
        end
        if modelFrame.GetModelFileID then
            fileID = modelFrame:GetModelFileID()
        end
    end)
    return displayID, fileID
end

local function DescribeSpeaker(db, speaker)
    if not speaker.speakerKey then
        return nil
    end
    local key = tostring(speaker.speakerKey)
    local npc = db.npcs[key] or {}
    npc.name = speaker.name or npc.name
    npc.isObject = speaker.isObject or nil
    local unit = Util.DialogUnit()
    if unit then
        npc.sex = UnitSex(unit)
        npc.creatureType = UnitCreatureType(unit)
        npc.level = UnitLevel(unit)
        local displayID, fileID = DisplayInfo(unit)
        npc.displayID = displayID or npc.displayID
        npc.modelFileID = fileID or npc.modelFileID
    end
    db.npcs[key] = npc
    return key
end

--- @param line table { kind, event, questID?, title?, text, speaker, found, pack? }
function Capture:Record(line)
    if not ns.db.capture or not line.text or line.text == "" then
        return
    end
    local db = GetDB()
    local npcKey = DescribeSpeaker(db, line.speaker)
    local mapID = C_Map.GetBestMapForUnit("player")
    local entry = {
        event = line.event,
        questID = line.questID,
        title = line.title,
        text = line.text,
        npc = npcKey,
        name = line.speaker.name,
        isObject = line.speaker.isObject or nil,
        found = line.found or nil,
        pack = line.pack and line.pack.name or nil,
        player = UnitName("player"),
        mapID = mapID,
        zone = GetZoneText(),
        subzone = GetSubZoneText(),
        build = select(2, GetBuildInfo()),
        time = time(),
    }
    if line.kind == "quest" then
        if not line.questID or line.questID == 0 then
            return
        end
        db.quests[format("%d-%s", line.questID, line.event)] = entry
    else
        db.gossip[format("%s|%s", npcKey or line.speaker.name or "?", Util.TextKey(line.text, entry.player))] = entry
    end
end

function Capture:Summary()
    local db = GetDB()
    local quests, questsMissing, gossip, gossipMissing = 0, 0, 0, 0
    for _, entry in pairs(db.quests) do
        quests = quests + 1
        if not entry.found then questsMissing = questsMissing + 1 end
    end
    for _, entry in pairs(db.gossip) do
        gossip = gossip + 1
        if not entry.found then gossipMissing = gossipMissing + 1 end
    end
    return quests, questsMissing, gossip, gossipMissing
end
