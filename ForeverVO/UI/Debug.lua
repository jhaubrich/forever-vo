local _, ns = ...
local Util, Packs = ns.Util, ns.Packs

--[[
Developer overlay: a small window attached to the quest or gossip frame,
showing the quest file base, the voice that plays, and the speaker's
display and model ids. Off by default. Saved variables do not come back
on this beta, so the checkbox keeps the choice in ForeverVO_devOverlay.
]]

local Debug = {}
ns.UI.Debug = Debug

local CVAR = "ForeverVO_devOverlay"

local probe
local panel
local current -- { host, head } while a dialog is up

local function Enabled()
    return ns.db and ns.db.devOverlay
end

local function VoiceLine(playing, recorded)
    if not playing or playing == "" then
        if recorded and recorded ~= "" then
            return "voice none · recorded " .. recorded
        end
        return "voice none"
    end
    if recorded and recorded ~= "" and recorded ~= playing then
        return format("voice %s · recorded %s", playing, recorded)
    end
    return "voice " .. playing
end

local function ModelLines()
    local display = (probe and probe.displayID and probe.displayID ~= 0) and tostring(probe.displayID) or "none"
    local model = (probe and probe.modelFileID and probe.modelFileID ~= 0) and tostring(probe.modelFileID) or "none"
    return "display " .. display, "model " .. model
end

--- Its own frame, stuck to the host's right edge, so the quest parchment
--- never has to make room for it.
local function Panel()
    if panel then
        return panel
    end
    panel = CreateFrame("Frame", nil, UIParent, "TooltipBackdropTemplate")
    panel:SetFrameStrata("DIALOG")
    panel:SetClampedToScreen(true)
    panel:EnableMouse(false)
    panel.text = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    panel.text:SetPoint("TOPLEFT", 12, -10)
    panel.text:SetJustifyH("LEFT")
    panel.text:SetSpacing(2)
    panel.watched = {}
    panel:Hide()
    return panel
end

local function Watch(host)
    local frame = Panel()
    if frame.watched[host] then
        return
    end
    frame.watched[host] = true
    host:HookScript("OnHide", function()
        if frame.host == host then
            frame:Hide()
            current = nil
        end
    end)
end

local Paint

local function Show(host, lines)
    if not Enabled() or not host then
        Debug:Hide()
        return
    end
    local frame = Panel()
    Watch(host)
    frame.host = host
    frame:ClearAllPoints()
    frame:SetPoint("TOPLEFT", host, "TOPRIGHT", 2, 0)
    current = { host = host, head = lines }
    Paint()
    frame:Show()
end

function Paint()
    if not current then
        return
    end
    local lines = {}
    for index, line in ipairs(current.head) do
        lines[index] = line
    end
    local display, model = ModelLines()
    lines[#lines + 1] = display
    lines[#lines + 1] = model
    local text = table.concat(lines, "\n")
    local body = panel.text
    body:SetWidth(400)
    body:SetText(text)
    local width = math.min(body:GetStringWidth(), 400)
    width = math.max(width, 96)
    body:SetWidth(width)
    panel:SetSize(width + 24, body:GetStringHeight() + 20)
end

function Debug:HideClosed()
    if not current then
        return
    end
    if not current.host:IsShown() then
        self:Hide()
    end
end

function Debug:Hide()
    current = nil
    if panel then
        panel:Hide()
    end
end

local function RepaintModel()
    Paint()
end

local function Probe()
    if not probe then
        probe = CreateFrame("PlayerModel", nil, UIParent)
        probe:SetSize(1, 1)
        probe:SetPoint("TOPLEFT")
        probe:SetAlpha(0)
        probe:EnableMouse(false)
        probe:SetScript("OnModelLoaded", function(self)
            if not self.pending then
                return
            end
            local ok, displayID = pcall(self.GetDisplayInfo, self)
            if ok and displayID and displayID ~= 0 then
                self.displayID = displayID
            end
            local okFile, fileID = pcall(self.GetModelFileID, self)
            if okFile and fileID and fileID ~= 0 then
                self.modelFileID = fileID
            end
            RepaintModel()
        end)
    end
    return probe
end

local function ReadModel()
    local frame = Probe()
    frame.pending = false
    frame.displayID, frame.modelFileID = nil, nil
    local unit = Util.DialogUnit()
    if not unit then
        return
    end
    frame.pending = true
    pcall(function()
        frame:SetUnit(unit)
        local displayID = frame:GetDisplayInfo()
        if displayID and displayID ~= 0 then
            frame.displayID = displayID
        end
        local fileID = frame:GetModelFileID()
        if fileID and fileID ~= 0 then
            frame.modelFileID = fileID
        end
    end)
end

local function SpeakerKey()
    local unit = Util.DialogUnit()
    local guid = unit and Util.Plain(UnitGUID(unit))
    local key = Util.SpeakerKeyFromGUID(guid)
    if not key then
        local name = unit and Util.Plain(UnitName(unit))
        key = Packs:SpeakerKeyByName(name)
    end
    return key
end

function Debug:ShowQuest(event)
    local questID = GetQuestID()
    local base, playing, recorded
    if questID and questID ~= 0 then
        base, playing, recorded = Packs:DebugQuest(questID, event)
    end
    ReadModel()
    Show(QuestFrame, {
        base or format("quest %s", questID or "?"),
        VoiceLine(playing, recorded),
    })
end

function Debug:ShowGossip(text, onGossipFrame)
    text = Util.Plain(text)
    local playing, recorded = Packs:DebugGossip(SpeakerKey(), text)
    ReadModel()
    local parent
    if onGossipFrame and GossipFrame then
        parent = GossipFrame
    elseif GossipFrame and GossipFrame:IsShown() then
        parent = GossipFrame
    else
        parent = QuestFrame
    end
    Show(parent, {
        VoiceLine(playing, recorded),
    })
end

--- Which quest panel is up, so turning the option on mid-dialog can redraw.
local function OpenQuestEvent()
    if QuestFrameDetailPanel and QuestFrameDetailPanel:IsShown() then
        return "accept"
    elseif QuestFrameProgressPanel and QuestFrameProgressPanel:IsShown() then
        return "progress"
    elseif QuestFrameRewardPanel and QuestFrameRewardPanel:IsShown() then
        return "complete"
    end
end

function Debug:Apply()
    if not Enabled() then
        self:Hide()
        return
    end
    local event = OpenQuestEvent()
    if event then
        self:ShowQuest(event)
    elseif GossipFrame and GossipFrame:IsShown() then
        self:ShowGossip(C_GossipInfo.GetText(), true)
    else
        self:Hide()
    end
end

ns.OnInit(function()
    pcall(C_CVar.RegisterCVar, CVAR, "0")
    local stored = C_CVar.GetCVar(CVAR)
    if stored == "1" then
        ns.db.devOverlay = true
    elseif stored == "0" then
        ns.db.devOverlay = false
    end

    local frame = CreateFrame("Frame")
    local handlers = {
        QUEST_DETAIL = function()
            Debug:ShowQuest("accept")
        end,
        QUEST_PROGRESS = function()
            Debug:ShowQuest("progress")
        end,
        QUEST_COMPLETE = function()
            Debug:ShowQuest("complete")
        end,
        QUEST_FINISHED = function()
            Debug:HideClosed()
        end,
        QUEST_GREETING = function()
            Debug:ShowGossip(GetGreetingText())
        end,
        GOSSIP_SHOW = function()
            Debug:ShowGossip(C_GossipInfo.GetText(), true)
        end,
        GOSSIP_CLOSED = function()
            Debug:HideClosed()
        end,
    }
    for event in pairs(handlers) do
        frame:RegisterEvent(event)
    end
    frame:SetScript("OnEvent", function(_, event)
        if not Enabled() then
            return
        end
        local ok, err = pcall(handlers[event])
        if not ok then
            ns.Print("|cffff4040error in developer overlay " .. event .. ":|r", err)
        end
    end)
end)
