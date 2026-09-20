local _, ns = ...
local Queue = ns.Queue

--[[
Slash commands and the addon compartment entry (the addon list button next to
the minimap in modern clients).
]]

local function Status()
    local packs = ns.Packs:Count()
    local quests, questsMissing, gossip, gossipMissing = ns.Capture:Summary()
    ns.Print(format("%d voice %s loaded. This session: %d quest %s (%d without audio), %d gossip %s (%d without audio).",
        packs, ns.Util.Plural(packs, "pack"), quests, ns.Util.Plural(quests, "text"), questsMissing,
        gossip, ns.Util.Plural(gossip, "text"), gossipMissing))
    if packs == 0 then
        ns.Print("No voice pack found. Install ForeverVO_Data next to ForeverVO.")
    end
end

local commands = {
    pause  = { "Pause playback", function() Queue:Pause() end },
    resume = { "Resume playback", function() Queue:Resume() end },
    skip   = { "Skip the current line", function() Queue:Skip() end },
    clear  = { "Clear the queue", function() Queue:Clear() end },
    replay = { "Replay the current line", function() Queue:Replay() end },
    queue  = { "Toggle the queue panel", function() ns.UI.QueueList:Toggle() end },
    head   = { "Toggle the talking head", function()
        ns.db.showHead = not ns.db.showHead
        ns.UI.TalkingHead:ApplySettings()
    end },
    reset  = { "Reset the talking head position", function()
        ns.UI.TalkingHead.frame:ResetPosition()
    end },
    status = { "Show loaded packs and capture counts", Status },
    debug  = { "Toggle debug messages", function()
        ns.db.debug = not ns.db.debug
        ns.Print("debug", ns.db.debug and "on" or "off")
    end },
}

local function Usage()
    ns.Print("/fvo opens the options. Commands:")
    for name, command in pairs(commands) do
        ns.Print(format("  |cffffd100/fvo %s|r  %s", name, command[1]))
    end
end

SLASH_FOREVERVO1 = "/fvo"
SLASH_FOREVERVO2 = "/forevervo"
SlashCmdList["FOREVERVO"] = function(input)
    local word = strtrim(input or ""):lower()
    if word == "" then
        ns.SettingsPanel:Open()
    elseif commands[word] then
        commands[word][2]()
    else
        Usage()
    end
end

-- Addon compartment ---------------------------------------------------------

function ForeverVO_OnCompartmentClick(_, buttonName, menuButtonFrame)
    if buttonName == "RightButton" and MenuUtil and MenuUtil.CreateContextMenu then
        MenuUtil.CreateContextMenu(menuButtonFrame or UIParent, function(_, root)
            root:CreateTitle("Forever Voiceover")
            root:CreateButton(Queue:IsPaused() and "Resume" or "Pause", function() Queue:TogglePause() end)
            root:CreateButton("Skip current line", function() Queue:Skip() end)
            root:CreateButton("Clear queue", function() Queue:Clear() end)
            root:CreateCheckbox("Show talking head", function() return ns.db.showHead end, function()
                ns.db.showHead = not ns.db.showHead
                ns.UI.TalkingHead:ApplySettings()
            end)
            root:CreateButton("Options", function() ns.SettingsPanel:Open() end)
        end)
    else
        ns.SettingsPanel:Open()
    end
end

function ForeverVO_OnCompartmentEnter(_, menuButtonFrame)
    GameTooltip:SetOwner(menuButtonFrame, "ANCHOR_LEFT")
    GameTooltip:SetText("Forever Voiceover")
    local size = Queue:Size()
    if size > 0 then
        GameTooltip:AddLine(format("%d %s queued%s", size, ns.Util.Plural(size, "line"), Queue:IsPaused() and " (paused)" or ""), 1, 1, 1)
    end
    GameTooltip:AddLine("Left-click: options. Right-click: playback menu.", 0.6, 0.6, 0.6)
    GameTooltip:Show()
end

function ForeverVO_OnCompartmentLeave()
    GameTooltip:Hide()
end

ns.OnLogin(function()
    if ns.Packs:Count() == 0 then
        ns.Print("no voice pack found. Install ForeverVO_Data next to ForeverVO, or generate one with the tools in the repository.")
    end
end)
