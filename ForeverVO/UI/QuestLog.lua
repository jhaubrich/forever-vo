local _, ns = ...
local Packs, Queue, Util = ns.Packs, ns.Queue, ns.Util

--[[
Play/stop buttons in front of quest titles in the quest log (QuestScrollFrame
from Blizzard_UIPanels_Game), for quests that have a voiced accept text.
]]

local BUTTON_SIZE = 18

local QuestLog = {
    buttons = {},   -- title frame -> button
    queued = {},    -- questID -> queue item started from the log
}
ns.UI.QuestLog = QuestLog

local function QuestItem(questID)
    local logIndex = C_QuestLog.GetLogIndexForQuestID(questID)
    local text = logIndex and GetQuestLogQuestText(logIndex) or nil
    local giver = Packs:QuestGiver(questID)
    local path, duration, pack = Packs:FindQuest(questID, "accept")
    return {
        kind = "quest", event = "accept", questID = questID,
        title = C_QuestLog.GetTitleForQuestID(questID) or "",
        text = text,
        name = Packs:SpeakerName(giver) or "Unknown",
        speakerKey = giver,
        isObject = giver ~= nil and not Util.IsCreatureKey(giver),
        path = path, duration = duration, pack = pack,
    }
end

function QuestLog:UpdateTexture(button)
    local item = button.questID and self.queued[button.questID]
    local playing = item and Queue:Contains(item)
    button:SetNormalTexture(ns.mediaPath .. (playing and "Stop" or "Play"))
end

function QuestLog:UpdateAllTextures()
    for _, button in pairs(self.buttons) do
        self:UpdateTexture(button)
    end
end

function QuestLog:OnClick(button)
    local questID = button.questID
    if not questID then
        return
    end
    local item = self.queued[questID]
    if item and Queue:Contains(item) then
        Queue:Remove(item)
        return
    end
    item = QuestItem(questID)
    item.onStop = function()
        if self.queued[questID] == item then
            self.queued[questID] = nil
        end
        self:UpdateAllTextures()
    end
    self.queued[questID] = item
    Queue:Add(item)
    self:UpdateAllTextures()
end

function QuestLog:GetButton(titleFrame)
    local button = self.buttons[titleFrame]
    if button then
        return button
    end
    button = CreateFrame("Button", nil, titleFrame)
    button:SetSize(BUTTON_SIZE, BUTTON_SIZE)
    button:SetPoint("TOPLEFT", titleFrame, "TOPLEFT", 9, -7)
    button:SetHitRectInsets(1, 1, 1, 1)
    button:SetNormalTexture(ns.mediaPath .. "Play")
    button:SetDisabledTexture(ns.mediaPath .. "Play")
    button:GetDisabledTexture():SetDesaturated(true)
    button:GetDisabledTexture():SetAlpha(0.33)
    button:SetHighlightTexture("Interface\\BUTTONS\\UI-Panel-MinimizeButton-Highlight", "ADD")
    button:SetScript("OnClick", function(self)
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        QuestLog:OnClick(self)
    end)
    button:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:SetText(self:IsEnabled() and "Play quest voiceover" or "No voiceover for this quest", 1, 1, 1)
        GameTooltip:Show()
    end)
    button:SetScript("OnLeave", GameTooltip_Hide)
    self.buttons[titleFrame] = button
    return button
end

function QuestLog:Update()
    if not QuestScrollFrame or not QuestScrollFrame.titleFramePool then
        return
    end
    for titleFrame in QuestScrollFrame.titleFramePool:EnumerateActive() do
        local questID = titleFrame.questID
        local button = self:GetButton(titleFrame)
        button.questID = questID
        local hasSound = questID ~= nil and Packs:FindQuest(questID, "accept") ~= nil
        button:SetEnabled(hasSound)
        self:UpdateTexture(button)
        button:Show()
    end
end

local function TryHook()
    if QuestLog.hooked then
        return true
    end
    if QuestLogQuests_Update then
        hooksecurefunc("QuestLogQuests_Update", function()
            QuestLog:Update()
        end)
        QuestLog.hooked = true
    end
    return QuestLog.hooked
end

ns.OnInit(function()
    if not TryHook() then
        -- The quest log lives in a Blizzard addon that may load after us
        EventUtil.ContinueOnAddOnLoaded("Blizzard_UIPanels_Game", TryHook)
    end
    Queue:RegisterCallback("OnPacksChanged", function()
        if QuestMapFrame and QuestMapFrame:IsShown() then
            QuestLog:Update()
        end
    end, QuestLog)
end)
