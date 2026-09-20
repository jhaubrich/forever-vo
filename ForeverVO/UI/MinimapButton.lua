local _, ns = ...
local Queue = ns.Queue

--[[
A round button on the minimap edge, drawn with the client's own tracking-button
border so it matches the rest. Left-click opens the options, right-click the
playback menu (same handlers as the addon compartment entry). Drag it around
the rim to move it; the angle is kept in the saved settings.
]]

local MinimapButton = {}
ns.UI.MinimapButton = MinimapButton

local BUTTON_SIZE = 32
local RADIUS_PADDING = 6

local function UpdatePosition(button)
    local angle = math.rad(ns.db.minimapAngle or 225)
    local radius = (Minimap:GetWidth() / 2) + RADIUS_PADDING
    button:ClearAllPoints()
    button:SetPoint("CENTER", Minimap, "CENTER", math.cos(angle) * radius, math.sin(angle) * radius)
end

local function DragTo(button)
    local mx, my = Minimap:GetCenter()
    local cx, cy = GetCursorPosition()
    local scale = Minimap:GetEffectiveScale()
    cx, cy = cx / scale, cy / scale
    ns.db.minimapAngle = math.deg(math.atan2(cy - my, cx - mx))
    UpdatePosition(button)
end

function MinimapButton:Create()
    local button = CreateFrame("Button", "ForeverVOMinimapButton", Minimap)
    self.button = button
    button:SetSize(BUTTON_SIZE, BUTTON_SIZE)
    button:SetFrameStrata("MEDIUM")
    button:SetFrameLevel(8)
    button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    button:RegisterForDrag("LeftButton")
    button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")

    local overlay = button:CreateTexture(nil, "OVERLAY")
    overlay:SetSize(53, 53)
    overlay:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
    overlay:SetPoint("TOPLEFT")

    local background = button:CreateTexture(nil, "BACKGROUND")
    background:SetSize(20, 20)
    background:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
    background:SetPoint("TOPLEFT", 7, -5)

    local icon = button:CreateTexture(nil, "ARTWORK")
    icon:SetSize(20, 20)
    icon:SetTexture(ns.iconTexture)
    icon:SetTexCoord(0.08, 0.92, 0.08, 0.92) -- trim the spell icon's dark frame
    icon:SetPoint("TOPLEFT", 6, -5)
    button.Icon = icon

    button:SetScript("OnClick", function(self, mouseButton)
        ForeverVO_OnCompartmentClick("ForeverVO", mouseButton, self)
    end)
    button:SetScript("OnEnter", function(self)
        ForeverVO_OnCompartmentEnter("ForeverVO", self)
    end)
    button:SetScript("OnLeave", ForeverVO_OnCompartmentLeave)
    button:SetScript("OnDragStart", function(self)
        if ns.db.lockMinimapButton then
            return
        end
        self:SetScript("OnUpdate", DragTo)
        self.Icon:SetTexCoord(0.12, 0.88, 0.12, 0.88)
    end)
    button:SetScript("OnDragStop", function(self)
        self:SetScript("OnUpdate", nil)
        self.Icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
    end)

    UpdatePosition(button)
    return button
end

function MinimapButton:ApplySettings()
    if not self.button then
        return
    end
    self.button:SetShown(ns.db.showMinimapButton ~= false)
    UpdatePosition(self.button)
end

ns.OnInit(function()
    MinimapButton:Create()
    MinimapButton:ApplySettings()
    -- The tooltip mentions the queue; refresh it when hovering during changes
    Queue:RegisterCallback("OnChanged", function()
        local button = MinimapButton.button
        if button and button:IsMouseOver() and GameTooltip:GetOwner() == button then
            ForeverVO_OnCompartmentEnter("ForeverVO", button)
        end
    end, MinimapButton)
end)
