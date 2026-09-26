local _, ns = ...
local Util = ns.Util

--[[
Records every quest and gossip line the player sees into the account-wide saved
variable ForeverVOCaptureDB, along with who said it and whether a pack had
audio for it. tools/ingest.py merges these files; tools/generate.py voices what
is missing. Nothing here affects playback.

The DB persists across sessions and characters since the beta started reading
saved variables back (confirmed 2026-09-25; before that every login began
empty). A key recorded since this login is remembered in `session`, so the
reminders can tell what this session added from what was already waiting.
]]

local Capture = {}
ns.Capture = Capture

local modelFrame
local session = {}   -- "quests:<key>" / "gossip:<key>" recorded since login

local function GetDB()
    local db = ForeverVOCaptureDB
    if type(db) ~= "table" then
        db = {}
        ForeverVOCaptureDB = db
    end
    -- 3: text is tokenised ($n/$c/$r) and class/race recorded
    -- 4: the reader's sex is recorded, and a voiced line the pack wants read
    --    again (Packs:QuestWanted) is captured with `wanted` set
    db.version = 4
    db.quests = db.quests or {}
    db.gossip = db.gossip or {}
    db.npcs = db.npcs or {}
    return db
end

--- Reads the creature display ID through an invisible PlayerModel; the pipeline
--- maps it to a race and gender to choose a voice. The model loads
--- asynchronously, so the value is written into the NPC record when it arrives.
local function GetModelFrame()
    if not modelFrame then
        modelFrame = CreateFrame("PlayerModel", nil, UIParent)
        modelFrame:SetSize(1, 1)
        modelFrame:SetPoint("TOPLEFT")
        modelFrame:SetAlpha(0)
        modelFrame:EnableMouse(false)
        modelFrame:SetScript("OnModelLoaded", function(self)
            local npc = self.pendingNPC
            if not npc then
                return
            end
            local ok, displayID = pcall(self.GetDisplayInfo, self)
            if ok and displayID and displayID ~= 0 then
                npc.displayID = displayID
            end
            local okFile, fileID = pcall(self.GetModelFileID, self)
            if okFile and fileID and fileID ~= 0 then
                npc.modelFileID = fileID
            end
        end)
    end
    return modelFrame
end

local function RequestDisplayInfo(unit, npc)
    pcall(function()
        local frame = GetModelFrame()
        frame.pendingNPC = npc
        frame:SetUnit(unit)
        -- If the model was already cached the callback may not fire; read now too
        local displayID = frame:GetDisplayInfo()
        if displayID and displayID ~= 0 then
            npc.displayID = displayID
        end
    end)
end

local function DescribeSpeaker(db, speaker)
    if not speaker.speakerKey then
        return nil
    end
    local key = tostring(speaker.speakerKey)
    local npc = db.npcs[key] or {}
    npc.name = speaker.name or npc.name
    npc.isObject = speaker.isObject or nil
    -- Only the unit that is this speaker: the "npc" unit lingers after its
    -- dialog closes and would lend its sex and model to a game object
    local unit = Util.DialogUnit()
    if unit and Util.Plain(UnitGUID(unit)) ~= speaker.guid then
        unit = nil
    end
    if unit then
        npc.sex = Util.Plain(UnitSex(unit))
        npc.creatureType = Util.Plain(UnitCreatureType(unit))
        npc.level = Util.Plain(UnitLevel(unit))
        if not npc.displayID or npc.displayID == 0 then
            RequestDisplayInfo(unit, npc)
        end
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
    -- The client resolves "$g lad:lass;" before we see the text, and unlike
    -- the name, class and race the other branch cannot be put back from one
    -- reading. The pipeline rebuilds it from a male and a female capture, so
    -- the pack asks for the sex it has not heard yet and this line is
    -- exported even though it played.
    local wanted = line.found and line.kind == "quest"
        and ns.Packs:QuestWanted(line.pack, line.questID, line.event) or nil
    local entry = {
        event = line.event,
        questID = line.questID,
        title = line.title,
        text = Util.Tokenize(line.text),
        npc = npcKey,
        name = line.speaker.name,
        isObject = line.speaker.isObject or nil,
        found = line.found or nil,
        wanted = wanted,
        pack = line.pack and line.pack.name or nil,
        player = UnitName("player"),
        class = UnitClass("player"),
        race = UnitRace("player"),
        sex = Util.PlayerSexLetter(),
        mapID = mapID,
        zone = GetZoneText(),
        subzone = GetSubZoneText(),
        build = select(2, GetBuildInfo()),
        -- The addon that wrote the line, so the pipeline can prefer a capture
        -- from a fixed release over one from a flawed earlier one, whatever
        -- their order in time, and ask for lines the old ones recorded again.
        addon = ns.version,
        time = time(),
    }
    if line.kind == "quest" then
        if not line.questID or line.questID == 0 then
            return
        end
        local key = format("%d-%s", line.questID, line.event)
        db.quests[key] = entry
        session["quests:" .. key] = true
    else
        local key = format("%s|%s", npcKey or line.speaker.name or "?", Util.TextKey(entry.text))
        db.gossip[key] = entry
        session["gossip:" .. key] = true
    end
    if ns.Export then
        ns.Export:OnLineCaptured(Capture.Contributes(entry))
    end
end

--- True when an export should carry the entry: no pack voiced it, or the pack
--- that did asked for this reader's version of it.
function Capture.Contributes(entry)
    return not entry.found or entry.wanted == true
end

--- Counts of lines in the capture and of those an export would carry, over the
--- whole DB and then over what this session recorded:
--- quests, questsMissing, gossip, gossipMissing, sessionSeen, sessionMissing.
function Capture:Summary()
    local db = GetDB()
    local quests, questsMissing, gossip, gossipMissing = 0, 0, 0, 0
    local sessionSeen, sessionMissing = 0, 0
    for key, entry in pairs(db.quests) do
        quests = quests + 1
        local contributes = Capture.Contributes(entry)
        if contributes then questsMissing = questsMissing + 1 end
        if session["quests:" .. key] then
            sessionSeen = sessionSeen + 1
            if contributes then sessionMissing = sessionMissing + 1 end
        end
    end
    for key, entry in pairs(db.gossip) do
        gossip = gossip + 1
        local contributes = Capture.Contributes(entry)
        if contributes then gossipMissing = gossipMissing + 1 end
        if session["gossip:" .. key] then
            sessionSeen = sessionSeen + 1
            if contributes then sessionMissing = sessionMissing + 1 end
        end
    end
    return quests, questsMissing, gossip, gossipMissing, sessionSeen, sessionMissing
end
