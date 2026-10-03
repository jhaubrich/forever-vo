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

local idleModelFrames = {} -- model frames whose one load has been read
local modelAsked = {}       -- speaker keys whose model this session has asked for
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
    -- 5: a voiced quest line whose live text is not the one the pack voiced
    --    (Packs:QuestTextMatches) is captured with `differs` set
    -- 6: an NPC's model is read on a frame of its own and carries the addon
    --    that read it; `recast` marks one that differs from the model its
    --    voice was cast from (Packs:SpeakerModel); `sexes` lists every sex
    --    the creature was met as
    db.version = 6
    db.quests = db.quests or {}
    db.gossip = db.gossip or {}
    db.npcs = db.npcs or {}
    return db
end

--- Keeps what a model frame read for the NPC record. A pack that cast this
--- speaker's voice from a model file records which (Packs:SpeakerModel); a
--- different one here means the voice may be another race's (Fizzlefuse, a
--- goblin, was voiced as an orc from a model an older release misread: #352),
--- so the record goes out with the next export even when its lines do not.
local function KeepModel(key, npc, displayID, fileID)
    if displayID and displayID ~= 0 then
        npc.displayID = displayID
    end
    if not fileID or fileID == 0 then
        return
    end
    npc.modelFileID = fileID
    npc.addon = ns.version
    local cast = ns.Packs:SpeakerModel(key)
    npc.recast = (cast and cast ~= fileID) and time() or nil
end

local function OnModelLoaded(self)
    local request = self.request
    if not request then
        return
    end
    self.request = nil
    local ok, displayID = pcall(self.GetDisplayInfo, self)
    local okFile, fileID = pcall(self.GetModelFileID, self)
    KeepModel(request.key, request.npc, ok and displayID, okFile and fileID)
    self:ClearModel()
    table.insert(idleModelFrames, self)
end

--- Reads the creature display and model file through an invisible PlayerModel;
--- the pipeline maps them to a race and gender to choose a voice. Each request
--- gets a frame no other NPC's model is loading into: releases before 0.1.7
--- shared one, and a model that finished loading after the player had moved on
--- was written onto the next speaker (a tauren's and an orc's onto Fizzlefuse).
--- A frame goes back to the pool only once its own load has been read; one
--- whose model was cached answers at once and may never fire, so it is left
--- alone rather than risk that load landing on someone else.
local function RequestModel(key, unit, npc)
    if modelAsked[key] then
        return
    end
    modelAsked[key] = true
    pcall(function()
        local frame = table.remove(idleModelFrames)
        if not frame then
            frame = CreateFrame("PlayerModel", nil, UIParent)
            frame:SetSize(1, 1)
            frame:SetPoint("TOPLEFT")
            frame:SetAlpha(0)
            frame:EnableMouse(false)
            frame:SetScript("OnModelLoaded", OnModelLoaded)
        end
        frame.request = { key = key, npc = npc }
        frame:SetUnit(unit)
        local fileID = frame:GetModelFileID()
        if fileID and fileID ~= 0 then
            KeepModel(key, npc, frame:GetDisplayInfo(), fileID)
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
        -- One creature ID can be either sex (Peacekeepers, city guards), and
        -- `sex` is only the last one met. `sexes` keeps every one, so the
        -- pipeline voices such a speaker's lines in both (#304).
        local letter = Util.SexLetter(npc.sex)
        if letter and not strfind(npc.sexes or "", letter, 1, true) then
            npc.sexes = (npc.sexes or "") .. letter
        end
        npc.creatureType = Util.Plain(UnitCreatureType(unit))
        npc.level = Util.Plain(UnitLevel(unit))
        if not npc.displayID or npc.displayID == 0 then
            RequestModel(key, unit, npc)
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
    -- A pack voiced this line from other text (Classic's is sometimes cut
    -- short, and Forever rewords): export it so the pipeline replaces it.
    local differs = line.found and line.kind == "quest"
        and not ns.Packs:QuestTextMatches(line.pack, line.questID, line.event, line.text) or nil
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
        differs = differs,
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

--- True when an export should carry the entry: no pack voiced it, the pack
--- that did asked for this reader's version of it, or voiced other text.
function Capture.Contributes(entry)
    return not entry.found or entry.wanted == true or entry.differs == true
end

--- Checks every line still waiting to be sent against the packs installed
--- now. `found`, `wanted` and `differs` were decided by the pack the player
--- had when the line was heard, so a line a later pack voiced, or stopped
--- asking for, stayed counted until an export. It never adds a line to send.
--- Quest text is checked as stored, already tokenised for its reader, and a
--- quest line recorded before the reader's sex was (0.1.4) is left alone.
function Capture:Refresh()
    local db = GetDB()
    for _, entry in pairs(db.quests) do
        if Capture.Contributes(entry) and not Capture.Exported(entry) and entry.questID
            and entry.sex then
            local path, _, pack = ns.Packs:FindQuest(entry.questID, entry.event)
            if path then
                local wanted = ns.Packs:QuestWanted(pack, entry.questID, entry.event, entry.sex) or nil
                local differs = entry.text
                    and not ns.Packs:QuestTextMatches(pack, entry.questID, entry.event, entry.text) or nil
                if not entry.found then
                    entry.found, entry.wanted, entry.differs = true, wanted, differs
                else
                    entry.wanted = entry.wanted and wanted
                    entry.differs = entry.differs and differs
                end
            end
        end
    end
    for _, entry in pairs(db.gossip) do
        if not entry.found and not Capture.Exported(entry) and entry.text then
            local speakerKey = tonumber(entry.npc) or ns.Packs:SpeakerKeyByName(entry.name)
            if ns.Packs:FindGossip(speakerKey, entry.text) then
                entry.found = true
            end
        end
    end
end

--- True when the entry was heard before the last export, which packed it.
--- Hearing the line again records a fresh time, so it goes out again as the
--- newer reading. Entries with no time predate 0.1.4 and count as old.
function Capture.Exported(entry)
    local at = GetDB().exportedAt
    return at ~= nil and (entry.time or 0) < at
end

--- Counts of lines in the capture and of those an export would carry (not
--- voiced, or wanted, and not packed by an earlier export), over the whole DB
--- and then over what this session recorded:
--- quests, questsMissing, gossip, gossipMissing, sessionSeen, sessionMissing.
function Capture:Summary()
    local db = GetDB()
    local quests, questsMissing, gossip, gossipMissing = 0, 0, 0, 0
    local sessionSeen, sessionMissing = 0, 0
    for key, entry in pairs(db.quests) do
        quests = quests + 1
        local contributes = Capture.Contributes(entry) and not Capture.Exported(entry)
        if contributes then questsMissing = questsMissing + 1 end
        if session["quests:" .. key] then
            sessionSeen = sessionSeen + 1
            if contributes then sessionMissing = sessionMissing + 1 end
        end
    end
    for key, entry in pairs(db.gossip) do
        gossip = gossip + 1
        local contributes = Capture.Contributes(entry) and not Capture.Exported(entry)
        if contributes then gossipMissing = gossipMissing + 1 end
        if session["gossip:" .. key] then
            sessionSeen = sessionSeen + 1
            if contributes then sessionMissing = sessionMissing + 1 end
        end
    end
    return quests, questsMissing, gossip, gossipMissing, sessionSeen, sessionMissing
end

--- What an export would carry, counted the way a player thinks of it: distinct
--- quests (an offer and its turn-in are one quest) and gossip lines.
function Capture:Pending()
    local db = GetDB()
    local quests, questCount, gossip = {}, 0, 0
    for _, entry in pairs(db.quests) do
        if Capture.Contributes(entry) and not Capture.Exported(entry) and entry.questID
            and not quests[entry.questID] then
            quests[entry.questID] = true
            questCount = questCount + 1
        end
    end
    for _, entry in pairs(db.gossip) do
        if Capture.Contributes(entry) and not Capture.Exported(entry) then
            gossip = gossip + 1
        end
    end
    return questCount, gossip
end

-- Before Welcome's handler counts what there is to send: Capture.lua loads first.
ns.OnLogin(function()
    Capture:Refresh()
end)
