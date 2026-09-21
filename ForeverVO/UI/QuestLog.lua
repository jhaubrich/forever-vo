local _, ns = ...
local Packs, Queue, Util = ns.Packs, ns.Queue, ns.Util

--[[
A "Play" button beside "Back" in the quest details panel (QuestMapFrame from
Blizzard_UIPanels_Game), for quests that have a voiced accept text.

There is deliberately no button on the quest list rows: the title frames put
their own status icon ("..." in progress, "?" ready to turn in) at the left of
the row, and anything anchored there covers it. The details panel is also where
the text is, so that is where the button to hear it belongs. Because the button
is parented to the details panel's BackFrame, it shows and hides with the panel
on its own.
]]

local QuestLog = {
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

function QuestLog:GetDetailsButton()
    if self.detailsButton then
        return self.detailsButton
    end
    local backFrame = QuestMapFrame and QuestMapFrame.DetailsFrame and QuestMapFrame.DetailsFrame.BackFrame
    if not backFrame or not backFrame.BackButton then
        return nil
    end
    local button = CreateFrame("Button", nil, backFrame, "UIPanelButtonTemplate")
    button:SetSize(70, 22)
    button:SetPoint("LEFT", backFrame.BackButton, "RIGHT", 6, 0)
    button:SetText("Play")
    button:SetScript("OnClick", function(self)
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        QuestLog:OnClick(self)
    end)
    button:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:SetText(self:IsEnabled() and "Play this quest's text" or "No voiceover for this quest", 1, 1, 1)
        GameTooltip:Show()
    end)
    button:SetScript("OnLeave", GameTooltip_Hide)
    self.detailsButton = button
    return button
end

function QuestLog:UpdateDetailsButton()
    local button = self:GetDetailsButton()
    if not button then
        return
    end
    local questID = QuestMapFrame.DetailsFrame.questID
    button.questID = questID
    local hasSound = questID ~= nil and Packs:FindQuest(questID, "accept") ~= nil
    button:SetEnabled(hasSound)
    local item = questID and self.queued[questID]
    button:SetText(item and Queue:Contains(item) and "Stop" or "Play")
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
        self:UpdateDetailsButton()
    end
    self.queued[questID] = item
    Queue:Add(item)
    self:UpdateDetailsButton()
end

local function TryHook()
    if QuestLog.hooked then
        return true
    end
    if QuestMapFrame_ShowQuestDetails then
        hooksecurefunc("QuestMapFrame_ShowQuestDetails", function()
            QuestLog:UpdateDetailsButton()
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
        if QuestMapFrame and QuestMapFrame.DetailsFrame and QuestMapFrame.DetailsFrame:IsShown() then
            QuestLog:UpdateDetailsButton()
        end
    end, QuestLog)
end)
