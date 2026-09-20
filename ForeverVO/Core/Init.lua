-- Forever Voiceover (ForeverVO): voiced quests and NPC dialogue for World of Warcraft: Forever.
--
-- Every file receives the private addon table `ns` through the vararg. Only two
-- globals are exported: `ForeverVO` (the same table, so voice packs can
-- register and users can poke at it) and the compartment click handlers that
-- the TOC requires by name.
local ADDON_NAME, ns = ...

ns.name = ADDON_NAME
ns.version = C_AddOns.GetAddOnMetadata(ADDON_NAME, "Version") or "dev"
ns.mediaPath = "Interface\\AddOns\\" .. ADDON_NAME .. "\\Media\\"
ns.UI = {}

ForeverVO = ns

-- ---------------------------------------------------------------------------
-- Saved variables
-- ---------------------------------------------------------------------------

ns.defaults = {
    -- Which dialogue to voice
    playAccept = true,
    playProgress = false,
    playComplete = true,
    playGreeting = true,
    playGossip = true,
    gossipFrequency = "oncePerQuestNPC", -- always | oncePerQuestNPC | oncePerNPC | never

    -- Audio
    soundChannel = "Master",             -- Master | Dialog | SFX | Music | Ambience
    muteGameDialog = true,
    stopOnClose = false,

    -- Talking head
    showHead = true,
    lockHead = false,
    headScale = 1,
    showText = true,
    showQueuePanel = false,

    -- Data collection for generating new voice lines
    capture = true,
    notifyUnvoiced = true,
    debug = false,
}

ns.charDefaults = {
    paused = false,
    seenGossip = {},
}

local function ApplyDefaults(target, defaults)
    for key, value in pairs(defaults) do
        if target[key] == nil then
            target[key] = type(value) == "table" and CopyTable(value) or value
        end
    end
    return target
end

function ns.Print(...)
    local text = strjoin(" ", tostringall(...))
    DEFAULT_CHAT_FRAME:AddMessage("|cff6ec6ffForever Voiceover|r " .. text)
end

function ns.Debug(...)
    if ns.db and ns.db.debug then
        ns.Print("|cff888888(debug)|r", ...)
    end
end

-- ---------------------------------------------------------------------------
-- Lifecycle
-- ---------------------------------------------------------------------------
-- Modules register an initializer; they run in order once saved variables are
-- available (ADDON_LOADED for this addon), then OnLogin runs at PLAYER_LOGIN.

ns.initializers = {}
ns.loginHandlers = {}

function ns.OnInit(func)
    table.insert(ns.initializers, func)
end

function ns.OnLogin(func)
    table.insert(ns.loginHandlers, func)
end

local loader = CreateFrame("Frame")
loader:RegisterEvent("ADDON_LOADED")
loader:RegisterEvent("PLAYER_LOGIN")
loader:SetScript("OnEvent", function(self, event, arg1)
    if event == "ADDON_LOADED" and arg1 == ADDON_NAME then
        ForeverVODB = ApplyDefaults(ForeverVODB or {}, ns.defaults)
        ForeverVOCharDB = ApplyDefaults(ForeverVOCharDB or {}, ns.charDefaults)
        ns.db = ForeverVODB
        ns.char = ForeverVOCharDB
        for _, init in ipairs(ns.initializers) do
            local ok, err = pcall(init)
            if not ok then
                ns.Print("|cffff4040initialization error:|r", err)
            end
        end
        self:UnregisterEvent("ADDON_LOADED")
    elseif event == "PLAYER_LOGIN" then
        for _, handler in ipairs(ns.loginHandlers) do
            local ok, err = pcall(handler)
            if not ok then
                ns.Print("|cffff4040login error:|r", err)
            end
        end
    end
end)
