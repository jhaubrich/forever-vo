local _, ns = ...
local Audio = ns.Audio

--[[
The playback queue. Items are plain tables:

  {
    kind = "quest" | "gossip",
    event = "accept" | "progress" | "complete" | "greeting" | "gossip",
    questID, title, text, name,        -- what is being said and by whom
    speakerKey, guid, isObject,        -- who to show in the portrait
    path, duration, pack,              -- resolved audio
    handle, timer,                     -- runtime playback state
    onStop,                            -- optional callback when removed
  }

Listeners subscribe with Queue:RegisterCallback("OnChanged", func, owner).
Events: OnChanged (any change), OnPlay(item), OnPause(paused).
]]

local Queue = CreateFromMixins(CallbackRegistryMixin)
ns.Queue = Queue
Queue:OnLoad()
Queue:GenerateCallbackEvents({ "OnChanged", "OnPlay", "OnPause", "OnPacksChanged" })

Queue.items = {}
Queue.nextID = 0

local TAIL_SILENCE = 0.5

function Queue:Size()
    return #self.items
end

function Queue:IsEmpty()
    return #self.items == 0
end

function Queue:Current()
    return self.items[1]
end

function Queue:Get(index)
    return self.items[index]
end

function Queue:Contains(item)
    for _, queued in ipairs(self.items) do
        if queued == item then
            return true
        end
    end
    return false
end

function Queue:IsPaused()
    return ns.char.paused
end

function Queue:IsPlaying()
    local current = self.items[1]
    return current ~= nil and current.timer ~= nil
end

local function StartPlayback(self, item)
    item.handle = Audio.Play(item.path)
    item.timer = C_Timer.NewTimer((item.duration or 0) + TAIL_SILENCE, function()
        item.timer = nil
        self:Remove(item, true)
    end)
    self:TriggerEvent("OnPlay", item)
end

local function StopPlayback(item)
    if item.timer then
        item.timer:Cancel()
        item.timer = nil
    end
    if item.handle then
        Audio.Stop(item.handle)
        item.handle = nil
    end
end

--- Adds an item whose audio has already been resolved (path + duration).
--- Returns true when it was queued.
function Queue:Add(item)
    if not item.path then
        return false
    end
    if not Audio.IsEnabled() then
        ns.Debug("sound is disabled in the game options")
        return false
    end
    for _, queued in ipairs(self.items) do
        if queued.path == item.path then
            return false -- already queued
        end
    end
    -- Gossip should not interrupt a run of quest text
    if item.kind == "gossip" then
        for _, queued in ipairs(self.items) do
            if queued.kind == "quest" then
                return false
            end
        end
    end
    if not Audio.Exists(item.path) then
        ns.Print(format("|cffff4040missing sound file|r %s (pack %s)", item.path, item.pack and item.pack.name or "?"))
        return false
    end

    self.nextID = self.nextID + 1
    item.id = self.nextID
    table.insert(self.items, item)

    if #self.items == 1 and not self:IsPaused() then
        StartPlayback(self, item)
    end
    self:TriggerEvent("OnChanged")
    return true
end

--- Removes an item. The first item is stopped if it is playing.
function Queue:Remove(item, finished)
    local index
    for i, queued in ipairs(self.items) do
        if queued == item then
            index = i
            break
        end
    end
    if not index then
        return
    end
    table.remove(self.items, index)
    if index == 1 then
        StopPlayback(item)
    end
    if item.onStop then
        item.onStop(item, finished)
    end

    if index == 1 and not self:IsPaused() then
        local nextItem = self.items[1]
        if nextItem then
            StartPlayback(self, nextItem)
        end
    end
    if self:IsEmpty() then
        Audio.Idle()
    end
    self:TriggerEvent("OnChanged")
end

function Queue:Skip()
    local current = self.items[1]
    if current then
        self:Remove(current)
    end
end

function Queue:Clear()
    while #self.items > 0 do
        self:Remove(self.items[#self.items])
    end
end

--- Removes every quest item for the given quest (used when a quest is abandoned).
function Queue:RemoveQuest(questID)
    for i = #self.items, 1, -1 do
        local item = self.items[i]
        if item.kind == "quest" and item.questID == questID then
            self:Remove(item)
        end
    end
end

function Queue:MoveToFront(item)
    local index
    for i, queued in ipairs(self.items) do
        if queued == item then
            index = i
            break
        end
    end
    if not index or index == 1 then
        return
    end
    local current = self.items[1]
    StopPlayback(current)
    table.remove(self.items, index)
    table.insert(self.items, 1, item)
    if not self:IsPaused() then
        StartPlayback(self, item)
    end
    self:TriggerEvent("OnChanged")
end

function Queue:Pause()
    if self:IsPaused() then
        return
    end
    ns.char.paused = true
    local current = self.items[1]
    if current then
        StopPlayback(current)
    end
    self:TriggerEvent("OnPause", true)
    self:TriggerEvent("OnChanged")
end

function Queue:Resume()
    if not self:IsPaused() then
        return
    end
    ns.char.paused = false
    local current = self.items[1]
    if current then
        StartPlayback(self, current)
    end
    self:TriggerEvent("OnPause", false)
    self:TriggerEvent("OnChanged")
end

function Queue:TogglePause()
    if self:IsPaused() then
        self:Resume()
    else
        self:Pause()
    end
end

--- Replays the current item from the start.
function Queue:Replay()
    local current = self.items[1]
    if current and not self:IsPaused() then
        StopPlayback(current)
        StartPlayback(self, current)
    end
end
