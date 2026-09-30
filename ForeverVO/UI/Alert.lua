local _, ns = ...

--[[
A message with an OK button, for errors the player has to see (the export
window's). It is a frame of the addon's own, not a StaticPopup: with the
client's gamepad UI on, every StaticPopup is handed to a protected path, and
one an addon opened freezes the client (issue #44, see Welcome.lua).
]]

local Alert = {}
ns.UI.Alert = Alert

local WIDTH = 360
local PADDING = 16
local BUTTON_WIDTH, BUTTON_HEIGHT = 110, 22

function Alert:GetFrame()
    if self.frame then
        return self.frame
    end
    local frame = CreateFrame("Frame", "ForeverVOAlert", UIParent)
    self.frame = frame
    frame:SetWidth(WIDTH)
    frame:SetPoint("TOP", UIParent, "TOP", 0, -135) -- where StaticPopup1 sits
    frame:SetFrameStrata("FULLSCREEN_DIALOG") -- above the export window
    frame:SetClampedToScreen(true)
    frame:EnableMouse(true)
    frame:Hide()
    tinsert(UISpecialFrames, "ForeverVOAlert") -- close with Escape

    frame.Border = CreateFrame("Frame", nil, frame, "DialogBorderDarkTemplate")
    frame.Border:SetAllPoints()

    frame.Title = frame:CreateFontString(nil, "ARTWORK", "GameFontNormalLarge")
    frame.Title:SetPoint("TOP", 0, -PADDING)
    frame.Title:SetText("Forever Voiceover")

    frame.Text = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
    frame.Text:SetJustifyH("CENTER")
    frame.Text:SetSpacing(2)
    frame.Text:SetWidth(WIDTH - 2 * PADDING)
    frame.Text:SetPoint("TOP", frame.Title, "BOTTOM", 0, -10)

    local ok = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    ok:SetSize(BUTTON_WIDTH, BUTTON_HEIGHT)
    ok:SetPoint("TOP", frame.Text, "BOTTOM", 0, -PADDING)
    ok:SetText(OKAY or "Okay")
    ok:SetScript("OnClick", function()
        frame:Hide()
    end)
    return frame
end

---@param text string
function Alert:Show(text)
    local frame = self:GetFrame()
    frame.Text:SetText(text)
    frame:SetHeight(PADDING + frame.Title:GetStringHeight() + 10 + frame.Text:GetStringHeight()
        + PADDING + BUTTON_HEIGHT + PADDING)
    frame:Show()
    frame:Raise()
end
