local _, ns = ...
local Util, Queue = ns.Util, ns.Queue

--[[
The talking head: a frame that mirrors Blizzard's own TalkingHeadFrame
(Blizzard_FrameXML/TalkingHeadUI.xml) in size, atlases, anchors and fade
animations, so it reads as part of the client. Shows the current queue item
with the speaker's model, name, title, and the spoken text paged in time with
the audio. Right-click skips, the X clears the queue.
]]

local FRAME_WIDTH, FRAME_HEIGHT = 570, 155
local MODEL_SIZE = 115
local TEXT_INSET = 28   -- left margin of the name and text when the portrait is hidden
local TALK_ANIMATION = 60
local MODEL_SETTLE = 0.5    -- seconds a model load gets before the book stands in
local PAGE_CHARS = 330

local TEXTURE_KIT_FORMATS = {
    TextBackground = "%s-TextBackground",
    Portrait = "%s-PortraitFrame",
    PortraitBg = "%s-PortraitBg",
}
local DEFAULT_ATLASES = {
    TextBackground = "TalkingHeads-TextBackground",
    Portrait = "TalkingHeads-Alliance-PortraitFrame",
    PortraitBg = "TalkingHeads-PortraitBg",
}
-- Name / Title / Text colors per texture kit. Blizzard's values for Name and
-- Text; Title is ours and must read on both the dark panel and the parchment.
local FONT_COLORS = {
    ["TalkingHeads-Horde"]    = { Name = CreateColor(0.28, 0.02, 0.02), Title = CreateColor(0.25, 0.15, 0.05), Text = CreateColor(0, 0, 0), Shadow = CreateColor(0, 0, 0, 0) },
    ["TalkingHeads-Alliance"] = { Name = CreateColor(0.02, 0.17, 0.33), Title = CreateColor(0.25, 0.15, 0.05), Text = CreateColor(0, 0, 0), Shadow = CreateColor(0, 0, 0, 0) },
    ["TalkingHeads-Neutral"]  = { Name = CreateColor(0.33, 0.16, 0.02), Title = CreateColor(0.25, 0.15, 0.05), Text = CreateColor(0, 0, 0), Shadow = CreateColor(0, 0, 0, 0) },
    ["Normal"]                = { Name = CreateColor(1, 0.82, 0.02),    Title = CreateColor(0.85, 0.85, 0.85), Text = CreateColor(1, 1, 1), Shadow = CreateColor(0, 0, 0, 1) },
}

local TalkingHead = {
    displayed = nil,
    pageTimers = {},
}
ns.UI.TalkingHead = TalkingHead

local function AtlasExists(atlas)
    return atlas ~= nil and C_Texture.GetAtlasExists(atlas)
end

local function CurrentTextureKit()
    if not ns.db.factionHead then
        return "Normal" -- Blizzard's default dark talking head
    end
    local faction = UnitFactionGroup("player")
    local kit = faction and ("TalkingHeads-" .. faction) or "TalkingHeads-Neutral"
    if AtlasExists(format(TEXTURE_KIT_FORMATS.TextBackground, kit)) then
        return kit
    end
    return "Normal"
end

local function Alpha(group, target, fromAlpha, toAlpha, duration, delay)
    local anim = group:CreateAnimation("Alpha")
    anim:SetTarget(target)
    anim:SetFromAlpha(fromAlpha)
    anim:SetToAlpha(toAlpha)
    anim:SetDuration(duration)
    anim:SetStartDelay(delay or 0)
    anim:SetOrder(1)
end

local function Scale(group, target, fromX, fromY, toX, toY, duration, delay, origin)
    local anim = group:CreateAnimation("Scale")
    anim:SetTarget(target)
    anim:SetScaleFrom(fromX, fromY)
    anim:SetScaleTo(toX, toY)
    anim:SetDuration(duration)
    anim:SetStartDelay(delay or 0)
    anim:SetOrder(1)
    if origin then
        anim:SetOrigin(origin, 0, 0)
    end
end

function TalkingHead.EventIcon(item)
    local event = item.event
    if event == "accept" then
        return ns.mediaPath .. "BulletAccept"
    elseif event == "progress" then
        return ns.mediaPath .. "BulletProgress"
    elseif event == "complete" then
        return ns.mediaPath .. "BulletComplete"
    end
    return ns.mediaPath .. "BulletGossip"
end

-- ---------------------------------------------------------------------------
-- Construction
-- ---------------------------------------------------------------------------

function TalkingHead:Init()
    self:CreateFrame()
    self:CreatePortrait()
    self:CreateText()
    self:CreateControls()
    self:CreateAnimations()
    self:ApplyTextureKit()
    self:ApplySettings()

    Queue:RegisterCallback("OnChanged", self.Update, self)
    Queue:RegisterCallback("OnPause", self.UpdatePause, self)
end

function TalkingHead:CreateFrame()
    local frame = CreateFrame("Button", "ForeverVOTalkingHead", UIParent)
    self.frame = frame
    frame:SetSize(FRAME_WIDTH, FRAME_HEIGHT)
    frame:SetFrameStrata("HIGH")
    frame:SetClampedToScreen(true)
    frame:SetMovable(true)
    frame:RegisterForClicks("RightButtonUp")
    frame:RegisterForDrag("LeftButton")
    frame:Hide()

    function frame:ResetPosition()
        self:ClearAllPoints()
        self:SetPoint("BOTTOM", UIParent, "BOTTOM", 0, 96) -- where Blizzard puts TalkingHeadFrame
    end
    frame:ResetPosition()
    frame:SetUserPlaced(true)

    frame:SetScript("OnClick", function(_, button)
        if button == "RightButton" then
            Queue:Skip()
        end
    end)
    frame:SetScript("OnDragStart", function(self)
        if not ns.db.lockHead then
            self:StartMoving()
        end
    end)
    frame:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
    end)

    frame.TextBackground = frame:CreateTexture(nil, "BACKGROUND")
    frame.TextBackground:SetPoint("CENTER")
    frame.TextBackground:SetAlpha(0.01)

    frame.Portrait = frame:CreateTexture(nil, "OVERLAY")
    frame.Portrait:SetPoint("TOPLEFT", 5, -6)
    frame.Portrait:SetAlpha(0.01)

    local function Glow(atlas, subLevel)
        local tex = frame:CreateTexture(nil, "OVERLAY", nil, subLevel or 0)
        tex:SetAtlas(atlas, true)
        tex:SetBlendMode("ADD")
        tex:SetAlpha(0.01)
        return tex
    end
    frame.Sheen = Glow("TalkingHeads-Glow-Sheen")
    frame.TextSheen = Glow("TalkingHeads-Glow-TextSheen")
    frame.GlowTop = Glow("TalkingHeads-Glow-TopBarGlow", 1)
    frame.GlowTop:SetPoint("CENTER", frame.Portrait, "TOP", 0, -11)
    frame.GlowLeft = Glow("TalkingHeads-Glow-SideBarGlow", 1)
    frame.GlowLeft:SetPoint("CENTER", frame.Portrait, "LEFT", 11, 25)
    frame.GlowRight = Glow("TalkingHeads-Glow-SideBarGlow", 1)
    frame.GlowRight:SetPoint("CENTER", frame.Portrait, "RIGHT", -11, 25)

    frame.CloseButton = CreateFrame("Button", nil, frame, "UIPanelCloseButtonNoScripts")
    frame.CloseButton:SetPoint("TOPRIGHT", -12, -12)
    frame.CloseButton:SetAlpha(0.01)
    frame.CloseButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_CLOSE)
        Queue:Clear()
    end)
end

function TalkingHead:CreatePortrait()
    local frame = self.frame
    local model = CreateFrame("PlayerModel", nil, frame)
    frame.Model = model
    model:SetSize(MODEL_SIZE, MODEL_SIZE)
    model:SetPoint("TOPLEFT", 21, -21)
    model:SetAlpha(0.01)

    model.PortraitBg = model:CreateTexture(nil, "BACKGROUND")
    model.PortraitBg:SetPoint("TOPLEFT")
    model.PortraitBg:SetAlpha(0.01)

    frame.Book = frame:CreateTexture(nil, "ARTWORK")
    frame.Book:SetTexture(ns.mediaPath .. "Book")
    frame.Book:SetSize(MODEL_SIZE - 20, MODEL_SIZE - 20)
    frame.Book:SetPoint("CENTER", model, "CENTER")
    frame.Book:Hide()

    model:SetScript("OnModelLoaded", function(self)
        pcall(self.SetPortraitZoom, self, 1)
        pcall(self.SetCamDistanceScale, self, 1)
        pcall(self.SetFacing, self, 0)
        self:Reveal()
        if self.talking then
            self:SetAnimation(TALK_ANIMATION)
        end
    end)

    --- GetModelFileID is the one model query that answers on this client.
    local function HasModel(self)
        local ok, fileID = pcall(self.GetModelFileID, self)
        return ok and fileID ~= nil and fileID ~= 0
    end

    local function Settle(self)
        self.settle = nil
        local hasModel = HasModel(self)
        -- A hidden PlayerModel drops its model, so it is only hidden once the
        -- load has had its chance, and shown again before the next load
        self:SetShown(hasModel)
        frame.Book:SetShown(not hasModel)
    end

    --- Shows the model once one is loaded, else the narrator's book: a speaker
    --- the client has no model for gets the book, never a leftover face. A
    --- load that has not answered yet is given a moment, since a failed one
    --- never fires OnModelLoaded.
    function model:Reveal()
        self:CancelSettle()
        if HasModel(self) then
            Settle(self)
        else
            self.settle = C_Timer.NewTimer(MODEL_SETTLE, function()
                Settle(self)
            end)
        end
    end

    function model:CancelSettle()
        if self.settle then
            self.settle:Cancel()
            self.settle = nil
        end
    end
    model:SetScript("OnAnimFinished", function(self)
        self:SetAnimation(self.talking and TALK_ANIMATION or 0)
    end)

    --- Loads the speaker's model. While the speaker is the unit on screen the
    --- model comes from that unit, the way the capture reads it: the client is
    --- drawing them, so it always has the model. SetCreature only knows what the
    --- creature cache holds, and on this client it loaded nothing for
    --- Varimathras and left the previous speaker (an orc) in the portrait. It
    --- stays as the fallback for a line that plays after the dialog is gone,
    --- cleared first so a failed load shows the book rather than the wrong
    --- face. The model may arrive later, in OnModelLoaded, which reveals it.
    function model:ShowCreature(creatureID, guid)
        self.talking = true
        local unit = guid and Util.DialogUnit()
        if unit and UnitGUID(unit) ~= guid then
            unit = nil
        end
        local loaded = unit and guid or creatureID
        if self.loaded == loaded then
            self:SetAnimation(TALK_ANIMATION)
            return
        end
        self.loaded = loaded
        self:CancelSettle()
        self:Show()
        frame.Book:Hide()
        if unit then
            self:SetUnit(unit)
        else
            self:ClearModel()
            self:SetCreature(creatureID)
        end
        self:Reveal()
    end

    function model:StopTalking()
        self.talking = false
    end
end

function TalkingHead:CreateText()
    local frame = self.frame

    frame.Name = frame:CreateFontString(nil, "ARTWORK")
    frame.Name:SetFontObject("Fancy22Font")
    frame.Name:SetJustifyH("LEFT")
    frame.Name:SetPoint("TOPLEFT", frame.Portrait, "TOPRIGHT", 2, -19)
    frame.Name:SetPoint("RIGHT", -42, 0)
    frame.Name:SetAlpha(0.01)

    frame.Title = frame:CreateFontString(nil, "ARTWORK")
    frame.Title:SetFontObject("GameFontNormal")
    frame.Title:SetJustifyH("LEFT")
    frame.Title:SetPoint("TOPLEFT", frame.Name, "BOTTOMLEFT", 1, -1)
    frame.Title:SetPoint("RIGHT", -42, 0)
    frame.Title:SetAlpha(0.01)

    frame.Text = frame:CreateFontString(nil, "ARTWORK")
    frame.Text:SetFontObject("GameFontHighlightLarge")
    frame.Text:SetJustifyH("LEFT")
    frame.Text:SetJustifyV("TOP")
    frame.Text:SetPoint("TOPLEFT", frame.Title, "BOTTOMLEFT", 0, -4)
    frame.Text:SetPoint("BOTTOMRIGHT", -42, 34)
    frame.Text:SetAlpha(0.01)
    if AutoScalingFontStringMixin then
        Mixin(frame.Text, AutoScalingFontStringMixin)
        frame.Text.minLineHeight = 12
    end

    frame.Sheen:SetPoint("LEFT", frame.Name, "LEFT", -48, 0)
    frame.TextSheen:SetPoint("LEFT", frame.Text, "LEFT", -48, 16)
end

function TalkingHead:CreateControls()
    local frame = self.frame

    frame.QueueText = frame:CreateFontString(nil, "ARTWORK")
    frame.QueueText:SetFontObject("GameFontDisableSmall")
    frame.QueueText:SetJustifyH("LEFT")
    frame.QueueText:SetPoint("BOTTOMLEFT", frame.Text, "BOTTOMLEFT", 0, -22)

    local function Button(text, width, onClick)
        local button = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        button:SetSize(width, 20)
        button:SetText(text)
        button:SetScript("OnClick", function()
            PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
            onClick()
        end)
        return button
    end

    frame.QueueButton = Button("Queue", 62, function()
        ns.UI.QueueList:Toggle()
    end)
    frame.QueueButton:SetPoint("BOTTOMRIGHT", -44, 12)

    frame.SkipButton = Button("Skip", 54, function()
        Queue:Skip()
    end)
    frame.SkipButton:SetPoint("RIGHT", frame.QueueButton, "LEFT", -4, 0)

    frame.PauseButton = Button("Pause", 66, function()
        Queue:TogglePause()
    end)
    frame.PauseButton:SetPoint("RIGHT", frame.SkipButton, "LEFT", -4, 0)
end

function TalkingHead:CreateAnimations()
    local frame = self.frame

    local fadeIn = frame:CreateAnimationGroup()
    fadeIn:SetToFinalAlpha(true)
    Alpha(fadeIn, frame.Model, 0, 1, 0.75)
    Alpha(fadeIn, frame.Model.PortraitBg, 0, 1, 0.75)
    Alpha(fadeIn, frame.Portrait, 0, 1, 0.75)
    Alpha(fadeIn, frame.TextBackground, 0, 1, 0.75, 0.4)
    Alpha(fadeIn, frame.Name, 0, 1, 0.25)
    Alpha(fadeIn, frame.Title, 0, 1, 0.25)
    Alpha(fadeIn, frame.Text, 0, 1, 0.25)
    Alpha(fadeIn, frame.CloseButton, 0, 1, 0.75, 0.75)
    Alpha(fadeIn, frame.GlowTop, 0, 0.7, 0.25, 0.15)
    Scale(fadeIn, frame.GlowTop, 0.25, 1, 1.5, 1, 0.25, 0.15)
    Alpha(fadeIn, frame.GlowTop, 0.7, 0, 0.5, 0.4)
    Alpha(fadeIn, frame.GlowLeft, 0, 0.7, 0.25, 0.35)
    Scale(fadeIn, frame.GlowLeft, 1, 0.5, 1, 1.6, 0.7, 0.35, "TOP")
    Alpha(fadeIn, frame.GlowLeft, 0.7, 0, 0.25, 0.85)
    Alpha(fadeIn, frame.GlowRight, 0, 0.7, 0.25, 0.35)
    Scale(fadeIn, frame.GlowRight, 1, 0.5, 1, 1.6, 0.7, 0.35, "TOP")
    Alpha(fadeIn, frame.GlowRight, 0.7, 0, 0.25, 0.95)
    Alpha(fadeIn, frame.Sheen, 0, 0.7, 0.5, 0.5)
    Scale(fadeIn, frame.Sheen, 0.25, 1, 1, 1, 0.25, 0.5, "LEFT")
    Alpha(fadeIn, frame.Sheen, 0.7, 0, 0.5, 1)
    Alpha(fadeIn, frame.TextSheen, 0, 0.7, 0.5, 0.75)
    Scale(fadeIn, frame.TextSheen, 0.25, 1, 1, 1, 0.25, 0.75, "LEFT")
    Alpha(fadeIn, frame.TextSheen, 0.7, 0, 0.5, 1.25)
    frame.FadeIn = fadeIn

    local newLine = frame:CreateAnimationGroup()
    newLine:SetToFinalAlpha(true)
    Alpha(newLine, frame.Sheen, 0, 0.7, 0.5, 0.2)
    Scale(newLine, frame.Sheen, 0.25, 1, 1, 1, 0.25, 0.2, "LEFT")
    Alpha(newLine, frame.Sheen, 0.7, 0, 0.5, 0.7)
    Alpha(newLine, frame.TextSheen, 0, 0.7, 0.5, 0.45)
    Scale(newLine, frame.TextSheen, 0.25, 1, 1, 1, 0.25, 0.45, "LEFT")
    Alpha(newLine, frame.TextSheen, 0.7, 0, 0.5, 0.95)
    frame.NewLine = newLine

    local textOut = frame:CreateAnimationGroup()
    textOut:SetToFinalAlpha(true)
    Alpha(textOut, frame.Name, 1, 0, 0.25)
    Alpha(textOut, frame.Title, 1, 0, 0.25)
    Alpha(textOut, frame.Text, 1, 0, 0.25)
    frame.TextOut = textOut

    local textIn = frame:CreateAnimationGroup()
    textIn:SetToFinalAlpha(true)
    Alpha(textIn, frame.Name, 0, 1, 0.25)
    Alpha(textIn, frame.Title, 0, 1, 0.25)
    Alpha(textIn, frame.Text, 0, 1, 0.25)
    frame.TextIn = textIn

    local close = frame:CreateAnimationGroup()
    close:SetToFinalAlpha(true)
    for _, region in ipairs({ frame.Model, frame.Model.PortraitBg, frame.Portrait, frame.TextBackground, frame.Name, frame.Title, frame.Text, frame.CloseButton }) do
        Alpha(close, region, 1, 0, 1)
    end
    close:SetScript("OnFinished", function()
        frame:Hide()
        frame.isClosing = nil
        ns.UI.QueueList:Update()
    end)
    frame.Close = close
end

-- ---------------------------------------------------------------------------
-- Styling and settings
-- ---------------------------------------------------------------------------

function TalkingHead:ApplyTextureKit()
    local frame = self.frame
    local kit = CurrentTextureKit()
    local regions = {
        TextBackground = frame.TextBackground,
        Portrait = frame.Portrait,
        PortraitBg = frame.Model.PortraitBg,
    }
    for key, region in pairs(regions) do
        local atlas = kit ~= "Normal" and format(TEXTURE_KIT_FORMATS[key], kit) or nil
        if not AtlasExists(atlas) then
            atlas = DEFAULT_ATLASES[key]
        end
        region:SetAtlas(atlas, true)
    end
    local colors = FONT_COLORS[kit] or FONT_COLORS["Normal"]
    frame.Name:SetTextColor(colors.Name:GetRGB())
    frame.Name:SetShadowColor(colors.Shadow:GetRGBA())
    frame.Text:SetTextColor(colors.Text:GetRGB())
    frame.Text:SetShadowColor(colors.Shadow:GetRGBA())
    frame.Title:SetTextColor(colors.Title:GetRGB())
    frame.Title:SetShadowColor(colors.Shadow:GetRGBA())
end

--- Shows or hides the portrait (model, ring, glows, book) and moves the name,
--- title and text to the frame's left edge while it is hidden. "Show the
--- talking head" is this; "Show the panel" is the whole frame.
function TalkingHead:LayoutPortrait()
    local frame = self.frame
    local shown = ns.db.showHead ~= false
    for _, region in ipairs({ frame.Portrait, frame.GlowTop, frame.GlowLeft, frame.GlowRight }) do
        region:SetShown(shown)
    end
    frame.Name:ClearAllPoints()
    if shown then
        frame.Name:SetPoint("TOPLEFT", frame.Portrait, "TOPRIGHT", 2, -19)
    else
        frame.Name:SetPoint("TOPLEFT", TEXT_INSET, -19)
    end
    frame.Name:SetPoint("RIGHT", -42, 0)
    if self.displayed then
        self:SetPortrait(self.displayed)
        if shown and frame:IsShown() and not frame.isClosing then
            -- the fade-in already ran while these were hidden
            frame.Model:SetAlpha(1)
            frame.Model.PortraitBg:SetAlpha(1)
            frame.Portrait:SetAlpha(1)
        end
    end
end

function TalkingHead:ApplySettings()
    local frame = self.frame
    frame:SetScale(ns.db.headScale or 1)
    self:ApplyTextureKit()
    frame.Text:SetShown(ns.db.showText ~= false)
    self:LayoutPortrait()
    if not ns.db.showPanel then
        self:CloseFrame()
    end
    self:Update()
end

-- ---------------------------------------------------------------------------
-- Display
-- ---------------------------------------------------------------------------

function TalkingHead:CancelPageTimers()
    for _, timer in ipairs(self.pageTimers) do
        timer:Cancel()
    end
    wipe(self.pageTimers)
end

function TalkingHead:ShowPagedText(item)
    self:CancelPageTimers()
    local frame = self.frame
    local pages = Util.Paginate(item.text, PAGE_CHARS)
    frame.Text:SetText(pages[1])
    if #pages == 1 or not item.duration then
        return
    end
    local total = 0
    for _, page in ipairs(pages) do
        total = total + #page
    end
    local elapsed = #pages[1]
    for i = 2, #pages do
        local at = item.duration * (elapsed / total)
        local page = pages[i]
        table.insert(self.pageTimers, C_Timer.NewTimer(at, function()
            if self.displayed == item then
                frame.Text:SetText(page)
            end
        end))
        elapsed = elapsed + #page
    end
end

function TalkingHead:SetPortrait(item)
    local frame = self.frame
    local model = frame.Model
    local shown = ns.db.showHead ~= false
    local creature = item.speakerKey and Util.IsCreatureKey(item.speakerKey)
    if shown and creature then
        model:ShowCreature(item.speakerKey, item.guid)
    else
        model:StopTalking()
        model:CancelSettle()
        model:ClearModel()
        model.loaded = nil
        model:Hide()
        frame.Book:SetShown(shown)
    end
end

function TalkingHead:Present(item)
    local frame = self.frame
    local wasShown = frame:IsShown() and not frame.isClosing
    local sameSpeaker = self.displayed and self.displayed.name == item.name

    frame.Close:Stop()
    frame.isClosing = nil
    self.displayed = item
    self:SetPortrait(item)

    local name = item.name or ""
    local title = item.title or ""
    if not wasShown then
        frame.Name:SetText(name)
        frame.Title:SetText(title)
        self:ShowPagedText(item)
        frame:Show()
        frame.FadeIn:Play()
    else
        frame.TextOut:Play()
        C_Timer.After(0.25, function()
            if self.displayed ~= item then return end
            frame.Name:SetText(name)
            frame.Title:SetText(title)
            self:ShowPagedText(item)
            frame.TextIn:Play()
            if not sameSpeaker then
                frame.NewLine:Play()
            end
        end)
    end
end

function TalkingHead:CloseFrame()
    local frame = self.frame
    self.displayed = nil
    self:CancelPageTimers()
    frame.Model:StopTalking()
    if frame:IsShown() and not frame.isClosing then
        frame.isClosing = true
        frame.Close:Play()
    end
end

function TalkingHead:Update()
    local frame = self.frame
    local current = Queue:Current()

    if not current or not ns.db.showPanel then
        self:CloseFrame()
    elseif current ~= self.displayed then
        self:Present(current)
    end

    local remaining = Queue:Size() - 1
    frame.QueueText:SetText(remaining > 0 and format("%d more queued", remaining) or "")
    frame.SkipButton:SetEnabled(Queue:Size() > 0)
    self:UpdatePause(Queue:IsPaused())
    ns.UI.QueueList:Update()
end

function TalkingHead:UpdatePause(paused)
    local frame = self.frame
    frame.PauseButton:SetText(paused and "Resume" or "Pause")
    if paused then
        frame.Model:StopTalking()
    elseif self.displayed then
        self:SetPortrait(self.displayed)
    end
end

ns.OnInit(function()
    TalkingHead:Init()
end)
