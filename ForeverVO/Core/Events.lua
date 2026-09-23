local _, ns = ...
local Util, Packs, Queue = ns.Util, ns.Packs, ns.Queue

--[[
Turns the client's quest and gossip events into queue items. Every event also
goes to Capture so that lines without audio can be generated later.
]]

local Events = {}
ns.Events = Events

local notified = {}
local function NotifyUnvoiced(what, key)
    -- One notice per quest event or per NPC per session, so a vendor's menus do not spam
    if not ns.db.notifyUnvoiced or (key and notified[key]) then
        return
    end
    if key then
        notified[key] = true
    end
    ns.Print(format("no voice yet for %s. |cffffd100/fvo export|r to contribute it.", what))
end

local currentQuestItem, currentGossipItem
local lastGossipOptions, selectedGossipOption
local dialogGUID   -- whoever opened the dialog that is up right now

-- ---------------------------------------------------------------------------
-- Speaker resolution
-- ---------------------------------------------------------------------------

--- Describes whoever the player is talking to right now.
---
--- The "npc" unit outlives its dialog: it still named High Executor Hadrec
--- three minutes after his frame closed, so the Corpse Laden Boat's turn-in
--- text was captured, voiced and portrayed as his, and a letter read next to
--- Gar'Thok was voiced as him. Blizzard's own quest frame names and portrays
--- the giver from "questnpc" alone, so quest events trust that unit, and
--- "npc" only while the dialog it opened is still up.
local function CurrentSpeaker(forQuest)
    local unit = Util.DialogUnit()
    if forQuest and unit == "npc" and UnitGUID("npc") ~= dialogGUID then
        unit = nil
    end
    local guid = unit and UnitGUID(unit)
    local name = unit and UnitName(unit)
    local key = Util.SpeakerKeyFromGUID(guid)
    if guid and (unit == "questnpc" or not forQuest) then
        dialogGUID = guid
    end
    return {
        guid = guid,
        name = name,
        speakerKey = key,
        isObject = unit == nil or (key ~= nil and key < 0),
    }
end

--- For quests handed out by items or shared by players, and turn-ins at a game
--- object the client leaves unattributed, fall back to the speaker recorded in
--- the packs so the portrait and name are still right.
local function ResolveQuestSpeaker(speaker, questID, event)
    if speaker.speakerKey or speaker.startItemID then
        return speaker
    end
    local key = Packs:QuestGiver(questID, event)
    if key then
        speaker.speakerKey = key
        speaker.name = Packs:SpeakerName(key) or speaker.name
        speaker.isObject = key < 0
    end
    speaker.name = speaker.name or "Unknown"
    return speaker
end

--- A quest started by reading an item: the item is the speaker, and the
--- narrator reads it under the book.
local function ItemSpeaker(itemID)
    local name = C_Item.GetItemNameByID(itemID) or C_Item.GetItemInfo(itemID)
    return { name = name, isObject = true, startItemID = itemID }
end

-- ---------------------------------------------------------------------------
-- Quest events
-- ---------------------------------------------------------------------------

local function QueueQuest(event, text, startItemID)
    local questID = GetQuestID()
    local title = GetTitleText()
    if not questID or questID == 0 then
        return
    end
    local speaker = startItemID and ItemSpeaker(startItemID) or CurrentSpeaker(true)
    speaker = ResolveQuestSpeaker(speaker, questID, event)
    local path, duration, pack, parts = Packs:FindQuest(questID, event)

    ns.Capture:Record({
        kind = "quest", event = event, questID = questID, title = title, text = text,
        speaker = speaker, found = path ~= nil, pack = pack,
    })

    if not path then
        NotifyUnvoiced(format("\"%s\" (%s)", title or questID, event), format("q%d-%s", questID, event))
        return
    end
    if (event == "accept" and not ns.db.playAccept)
        or (event == "progress" and not ns.db.playProgress)
        or (event == "complete" and not ns.db.playComplete) then
        return
    end

    local item = {
        kind = "quest", event = event, questID = questID, title = title, text = text,
        name = speaker.name, speakerKey = speaker.speakerKey, guid = speaker.guid, isObject = speaker.isObject,
        path = path, duration = duration, pack = pack, parts = parts,
    }
    if Queue:Add(item) then
        currentQuestItem = item
    end
end

function Events.QUEST_DETAIL(questStartItemID)
    -- The client says when a quest was started from an item; nothing else does
    if questStartItemID == 0 then
        questStartItemID = nil
    end
    QueueQuest("accept", GetQuestText(), questStartItemID)
end

function Events.QUEST_PROGRESS()
    QueueQuest("progress", GetProgressText())
end

function Events.QUEST_COMPLETE()
    QueueQuest("complete", GetRewardText())
end

function Events.QUEST_FINISHED()
    if ns.db.stopOnClose and currentQuestItem then
        Queue:Remove(currentQuestItem)
    end
    currentQuestItem = nil
    dialogGUID = nil
end

-- ---------------------------------------------------------------------------
-- Gossip events
-- ---------------------------------------------------------------------------

local function ShouldPlayGossip(speaker)
    local frequency = ns.db.gossipFrequency
    if frequency == "never" then
        return false
    end
    local npcKey = speaker.guid or speaker.name or "unknown"
    local seen = ns.char.seenGossip[npcKey]
    if frequency == "oncePerNPC" and seen then
        return false
    end
    if frequency == "oncePerQuestNPC" and seen then
        local hasQuests = C_GossipInfo.GetNumActiveQuests() > 0 or C_GossipInfo.GetNumAvailableQuests() > 0
        if hasQuests then
            return false
        end
    end
    return true, npcKey
end

local function QueueGossip(event, text)
    if not text or text == "" then
        return
    end
    local speaker = CurrentSpeaker()
    if not speaker.guid and not speaker.name then
        return -- dialog opened while a menu was up; nothing to attribute it to
    end
    local speakerKey = speaker.speakerKey or Packs:SpeakerKeyByName(speaker.name)
    local path, duration, pack, parts = Packs:FindGossip(speakerKey, text)

    ns.Capture:Record({
        kind = "gossip", event = event, text = text, title = selectedGossipOption,
        speaker = speaker, found = path ~= nil, pack = pack,
    })

    if not path then
        NotifyUnvoiced(format("%s's %s", speaker.name or "this NPC", event == "greeting" and "greeting" or "gossip"), speaker.guid or speaker.name)
        return
    end
    if (event == "greeting" and not ns.db.playGreeting) or (event == "gossip" and not ns.db.playGossip) then
        return
    end
    local play, npcKey = ShouldPlayGossip(speaker)
    if not play then
        return
    end

    local item = {
        kind = "gossip", event = event, text = text,
        title = selectedGossipOption and format("\"%s\"", selectedGossipOption) or nil,
        name = speaker.name, speakerKey = speakerKey, guid = speaker.guid, isObject = speaker.isObject,
        path = path, duration = duration, pack = pack, parts = parts,
    }
    if Queue:Add(item) then
        currentGossipItem = item
        ns.char.seenGossip[npcKey] = true
    end
end

function Events.QUEST_GREETING()
    QueueGossip("greeting", GetGreetingText())
end

function Events.GOSSIP_SHOW()
    QueueGossip("gossip", C_GossipInfo.GetText())
    selectedGossipOption = nil
    lastGossipOptions = C_GossipInfo.GetOptions()
end

function Events.GOSSIP_CLOSED()
    if ns.db.stopOnClose and currentGossipItem then
        Queue:Remove(currentGossipItem)
    end
    currentGossipItem = nil
    selectedGossipOption = nil
    dialogGUID = nil
end

-- ---------------------------------------------------------------------------
-- Registration
-- ---------------------------------------------------------------------------

ns.OnInit(function()
    local frame = CreateFrame("Frame")
    for event, handler in pairs(Events) do
        if type(handler) == "function" then
            frame:RegisterEvent(event)
        end
    end
    frame:SetScript("OnEvent", function(_, event, ...)
        local ok, err = pcall(Events[event], ...)
        if not ok then
            ns.Print("|cffff4040error in " .. event .. ":|r", err)
        end
    end)

    -- Remember which gossip option was picked so the talking head can title it
    hooksecurefunc(C_GossipInfo, "SelectOption", function(optionID)
        if lastGossipOptions then
            for _, info in ipairs(lastGossipOptions) do
                if info.gossipOptionID == optionID then
                    selectedGossipOption = info.name
                    break
                end
            end
        end
    end)

    -- Drop queued lines for quests the player abandons
    hooksecurefunc(C_QuestLog, "AbandonQuest", function()
        local questID = C_QuestLog.GetAbandonQuest()
        if questID then
            Queue:RemoveQuest(questID)
        end
    end)
end)
