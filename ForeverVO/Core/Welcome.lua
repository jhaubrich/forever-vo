local _, ns = ...
local Util = ns.Util

--[[
The login prompt. The first login with the addon shows it once to explain where
the voices come from; after that it shows at login whenever the capture holds
lines an export would carry (Capture:Pending), until the player ticks the
opt-out box, which sets crowdsourceOptOut (the same box is on the addon's
own page under Options, to opt back in). "Send Quests to Project" opens the /fvo export window.

The "seen" flag is kept in saved variables (welcomed) and in an
addon-registered CVar. The CVar came first, from when this beta did not load
saved variables back, but it does not outlive the client either: it holds
through a /reload and is gone at the next login, which showed the
introduction, opt-out ignored, on every login (2026-09-30). The opt-out now
wins outright, and ticking it counts as having seen the introduction.

The prompt is a frame of the addon's own, not a StaticPopup. With the client's
alpha gamepad UI on, every StaticPopup that opens is handed to the gamepad
frame manager, which ends in the protected SetPreferredGamepadInteractTarget;
a popup an addon opened taints that path, the call is blocked, and the
"blocked action" popup that reports it runs the same path again, freezing the
client (issue #44). The manager only watches StaticPopups, UI panels and menus,
so a plain frame shown directly never reaches it.
]]

local Welcome = {}
ns.Welcome = Welcome

local CVAR = "ForeverVO_welcomed"
local WIDTH = 420 -- the width of a wide StaticPopup
local PADDING = 16
local BUTTON_WIDTH, BUTTON_HEIGHT, BUTTON_GAP = 168, 22, 8
local CHECK_SIZE = 26
local TITLE_GAP = 10

local INTRO = "This addon reads quests and NPC dialogue aloud in voices made by players, not by Blizzard. Quest text is not in the game files, so a line can only be voiced once someone has seen it and sent it in."

local function HasSeen()
    return ns.db.welcomed or ns.db.crowdsourceOptOut or C_CVar.GetCVar(CVAR) == "1"
end

local function MarkSeen()
    ns.db.welcomed = true
    C_CVar.SetCVar(CVAR, "1")
end

--- How many there are to send, counted as the Send button counts them.
local function Pending()
    return ns.Export:PendingCount()
end

function Welcome:GetFrame()
    if self.frame then
        return self.frame
    end
    local frame = CreateFrame("Frame", "ForeverVOWelcome", UIParent)
    self.frame = frame
    frame:SetWidth(WIDTH)
    frame:SetPoint("TOP", UIParent, "TOP", 0, -135) -- where StaticPopup1 sits
    frame:SetFrameStrata("DIALOG")
    frame:SetClampedToScreen(true)
    frame:EnableMouse(true)
    frame:Hide()
    tinsert(UISpecialFrames, "ForeverVOWelcome") -- close with Escape

    frame.Border = CreateFrame("Frame", nil, frame, "DialogBorderDarkTemplate")
    frame.Border:SetAllPoints()

    frame.Title = frame:CreateFontString(nil, "ARTWORK", "GameFontNormalLarge")
    frame.Title:SetPoint("TOP", 0, -PADDING)
    frame.Title:SetText("Forever Voiceover")

    frame.Text = frame:CreateFontString(nil, "ARTWORK")
    frame.Text:SetFontObject("GameFontHighlight")
    frame.Text:SetJustifyH("CENTER")
    frame.Text:SetWidth(WIDTH - 2 * PADDING)
    frame.Text:SetPoint("TOP", frame.Title, "BOTTOM", 0, -TITLE_GAP)

    local send = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    send:SetSize(BUTTON_WIDTH, BUTTON_HEIGHT)
    send:SetPoint("TOPRIGHT", frame.Text, "BOTTOM", -BUTTON_GAP / 2, -PADDING)
    send:SetText("Send Quests to Project")
    send:SetScript("OnClick", function()
        frame:Hide()
        ns.Export:Show(false)
    end)
    frame.Send = send

    local later = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    later:SetSize(BUTTON_WIDTH, BUTTON_HEIGHT)
    later:SetPoint("TOPLEFT", frame.Text, "BOTTOM", BUTTON_GAP / 2, -PADDING)
    later:SetText("Later")
    later:SetScript("OnClick", function()
        frame:Hide()
    end)
    frame.Later = later

    local optOut = CreateFrame("CheckButton", nil, frame, "UICheckButtonTemplate")
    optOut:SetSize(CHECK_SIZE, CHECK_SIZE)
    optOut.Text:SetFontObject("GameFontHighlightSmall")
    optOut.Text:SetText("Don't Show This Again (Opt out of crowdsourcing)")
    optOut.Text:SetPoint("LEFT", optOut, "RIGHT", 2, 0)
    optOut:SetScript("OnClick", function(box)
        ns.db.crowdsourceOptOut = box:GetChecked() and true or false
    end)
    frame.OptOut = optOut

    return frame
end

--- Fill in the text for what the capture holds now and size the frame to it.
---@param intro boolean lead with what the addon is, for a first login
function Welcome:Refresh(intro)
    local frame = self:GetFrame()
    local pending = Pending()
    local lines = {}
    if intro then
        table.insert(lines, INTRO)
        table.insert(lines, "")
    end
    if pending > 0 then
        table.insert(lines, format("You have |cffffd100%d|r %s we need! Click |cff66bbffSend|r so we can voice them.", pending, Util.Plural(pending, "quest")))
        table.insert(lines, "")
        table.insert(lines, "Sending opens a GitHub issue, so you need a free GitHub account.")
    else
        table.insert(lines, "Nothing to send yet. Play with the addon on, and you will be asked here when you have lines to contribute, or type |cffffd100/fvo export|r any time.")
    end
    frame.Text:SetText(table.concat(lines, "\n"))

    frame.Send:SetEnabled(pending > 0)
    frame.OptOut:SetChecked(ns.db.crowdsourceOptOut)
    local textHeight = frame.Text:GetStringHeight()
    local checkTop = PADDING + frame.Title:GetStringHeight() + TITLE_GAP + textHeight + PADDING + BUTTON_HEIGHT + PADDING / 2
    -- Centre the box and its label together under the buttons.
    frame.OptOut:ClearAllPoints()
    frame.OptOut:SetPoint("TOPLEFT", frame, "TOP", -(CHECK_SIZE + 2 + frame.OptOut.Text:GetStringWidth()) / 2, -checkTop)
    frame:SetHeight(checkTop + CHECK_SIZE + PADDING / 2)
end

---@param intro boolean|nil lead with the introduction (default: only before it was seen)
function Welcome:Show(intro)
    if intro == nil then
        intro = not HasSeen()
    end
    self:Refresh(intro)
    self:GetFrame():Show()
    MarkSeen()
end

ns.OnInit(function()
    pcall(C_CVar.RegisterCVar, CVAR, "0")
end)

ns.OnLogin(function()
    if ns.db.crowdsourceOptOut then
        return
    end
    local first = not HasSeen()
    if not first and Pending() == 0 then
        return
    end
    C_Timer.After(4, function()
        Welcome:Show(first)
    end)
end)
