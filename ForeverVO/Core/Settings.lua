local _, ns = ...

--[[
Options panel built on the Settings API (Escape > Options > AddOns).
Boolean settings write straight into ns.db; dropdowns and the slider go through
proxy settings so the saved values stay readable strings/numbers.
]]

local SettingsPanel = {}
ns.SettingsPanel = SettingsPanel

-- The third field is the tooltip on that entry of the dropdown
local GOSSIP_REPEAT = {
    { "always",      "Every time",
      "Greetings and gossip are read every time you talk to an NPC." },
    { "gossipOnce",  "Gossip once, greetings always",
      "An NPC's gossip is read the first time you talk to them and not after. Greetings are read every time." },
    { "once",        "Both once per NPC",
      "An NPC's greeting and their gossip are each read the first time and not after." },
    { "questGivers", "Once for quest givers",
      "NPCs with a quest for you have their greeting and gossip read once; everyone else every time." },
}
local SOUND_CHANNELS = { "Master", "Dialog", "SFX", "Music", "Ambience" }

--- Narrator voices the installed packs offer; resolved when the dropdown opens.
local function NarratorChoices()
    local choices = {}
    for _, voice in ipairs(ns.Packs:NarratorVoices()) do
        table.insert(choices, { voice, ns.Packs.NarratorVoiceLabel(voice) })
    end
    return choices
end

local function Checkbox(category, key, name, tooltip, onChange)
    local setting = Settings.RegisterAddOnSetting(category, "FVO_" .. key, key, ns.db, Settings.VarType.Boolean, name, ns.defaults[key])
    if onChange then
        setting:SetValueChangedCallback(function(_, value)
            onChange(value)
        end)
    end
    Settings.CreateCheckbox(category, setting, tooltip)
    return setting
end

local function Dropdown(category, key, name, tooltip, choices, onChange)
    -- choices: list of { value, label }, or a function returning one for choices
    -- that depend on the installed voice packs (they register after this panel
    -- is built). The stored value is choices[i][1]; the control sees the index.
    local function Choices()
        return type(choices) == "function" and choices() or choices
    end
    local function GetValue()
        for index, choice in ipairs(Choices()) do
            if choice[1] == ns.db[key] then
                return index
            end
        end
        return 1
    end
    local function SetValue(index)
        local choice = Choices()[index]
        if not choice then
            return
        end
        ns.db[key] = choice[1]
        if onChange then
            onChange(ns.db[key])
        end
    end
    local function GetOptions()
        local container = Settings.CreateControlTextContainer()
        for index, choice in ipairs(Choices()) do
            container:Add(index, choice[2], choice[3])
        end
        return container:GetData()
    end
    local default = 1
    for index, choice in ipairs(Choices()) do
        if choice[1] == ns.defaults[key] then
            default = index
        end
    end
    local setting = Settings.RegisterProxySetting(category, "FVO_" .. key, Settings.VarType.Number, name, default, GetValue, SetValue)
    Settings.CreateDropdown(category, setting, GetOptions, tooltip)
    return setting
end

local function Slider(category, key, name, tooltip, minValue, maxValue, step, formatter, onChange)
    local function GetValue()
        return ns.db[key]
    end
    local function SetValue(value)
        ns.db[key] = value
        if onChange then
            onChange(value)
        end
    end
    local setting = Settings.RegisterProxySetting(category, "FVO_" .. key, Settings.VarType.Number, name, ns.defaults[key], GetValue, SetValue)
    local options = Settings.CreateSliderOptions(minValue, maxValue, step)
    options:SetLabelFormatter(MinimalSliderWithSteppersMixin.Label.Right, formatter)
    Settings.CreateSlider(category, setting, options, tooltip)
    return setting
end

local VERSION_LINE_HEIGHT = 18
local VERSION_GAP = 10

--- The installed voice packs by their full titles, or a note that there are
--- none. Read when the page is shown: the packs register after this panel.
local function PackLines()
    local packs = ns.Packs:Versions(true)
    if #packs == 0 then
        return { "None installed" }
    end
    return packs
end

--- A section header with the addon's version under its title, then a "Voice
--- packs" subtitle and one line per pack. The header frame is pooled with
--- every other header in the Settings list, so the extra text is hidden again
--- whenever the frame is reused for another one.
local function VersionsInitializer()
    local initializer = CreateSettingsListSectionHeaderInitializer("Versions",
        "Quote these when reporting a bug.")
    local baseExtent = initializer.GetExtent
    function initializer:GetExtent()
        return baseExtent(self) + 2 * VERSION_GAP + (2 + #PackLines()) * VERSION_LINE_HEIGHT
    end
    local baseInit = initializer.InitFrame
    function initializer:InitFrame(frame)
        baseInit(self, frame)
        if not frame.FVOVersions then
            local function Text(font, anchor)
                local text = frame:CreateFontString(nil, "OVERLAY", font)
                text:SetJustifyH("LEFT")
                text:SetSpacing(VERSION_LINE_HEIGHT - 12)
                text:SetPoint("TOPLEFT", anchor, "BOTTOMLEFT", 0, -VERSION_GAP)
                return text
            end
            local addon = Text("GameFontHighlight", frame.Title)
            local subtitle = Text("GameFontNormal", addon)
            subtitle:SetText("Voice packs")
            local packs = Text("GameFontHighlight", subtitle)
            packs:SetPoint("TOPLEFT", subtitle, "BOTTOMLEFT", 0, -(VERSION_LINE_HEIGHT - 12))
            frame.FVOVersions = { addon = addon, subtitle = subtitle, packs = packs }
            hooksecurefunc(frame, "Init", function(header, other)
                for _, text in pairs(header.FVOVersions) do
                    text:SetShown(other == initializer)
                end
            end)
        end
        local texts = frame.FVOVersions
        texts.addon:SetText("Forever Voiceover " .. (ns.version or "dev"))
        texts.packs:SetText(table.concat(PackLines(), "\n"))
        for _, text in pairs(texts) do
            text:Show()
        end
    end
    return initializer
end

--- "Send Quests to Project" beside the Defaults button in the page header,
--- shown only while this addon's main page is. The header belongs to the
--- Settings panel and is shared by every page, so the button follows the
--- layout the panel displays (a search result is a layout of its own).
local function AddHeaderButton(layout)
    local header = _G.SettingsPanel:GetSettingsList().Header
    local button = CreateFrame("Button", nil, header, "UIPanelButtonTemplate")
    button:SetSize(180, 22)
    button:SetPoint("RIGHT", header.DefaultsButton, "LEFT", -8, 0)
    button:SetText("Send Quests to Project")
    button:Hide()
    button:SetScript("OnClick", function()
        _G.SettingsPanel:Close(true)
        ns.Export:Show(false)
    end)
    button:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_BOTTOM")
        GameTooltip:SetText("Send Quests to Project", 1, 0.82, 0)
        GameTooltip:AddLine("Copy a link that opens a GitHub issue with the quest and NPC lines you have heard since your last export that the voice pack does not have yet. The same as /fvo export.", 1, 1, 1, true)
        GameTooltip:Show()
    end)
    button:SetScript("OnLeave", GameTooltip_Hide)
    hooksecurefunc(_G.SettingsPanel, "DisplayLayout", function(_, shown)
        button:SetShown(shown == layout)
    end)
end

function SettingsPanel:Open()
    if self.category then
        Settings.OpenToCategory(self.category:GetID())
    end
end

ns.OnInit(function()
    local category, layout = Settings.RegisterVerticalLayoutCategory("Forever Voiceover")
    SettingsPanel.category = category
    local head = ns.UI.TalkingHead

    local function RefreshHead()
        head:ApplySettings()
    end

    -- On the addon's own page, where a player looking to opt back in lands first
    Checkbox(category, "crowdsourceOptOut", "Opt out of crowdsourcing", "Stop the window at login that offers to send quests and NPC lines the voice pack does not have yet. Clear it to be asked again. /fvo export works either way.")
    AddHeaderButton(layout)

    -- What to voice
    local voiced = Settings.RegisterVerticalLayoutSubcategory(category, "What to voice")
    Checkbox(voiced, "playAccept", "Quest offers", "Read the quest text when a quest is offered.")
    Checkbox(voiced, "playComplete", "Quest turn-ins", "Read the reward text when handing in a quest.")
    Checkbox(voiced, "playProgress", "Quest progress", "Read the 'not yet complete' text when returning early. Usually best left off.")
    Checkbox(voiced, "playGreeting", "Greetings", "Read the greeting an NPC with more than one quest opens with, above the list of their quests.")
    Checkbox(voiced, "playGossip", "Gossip", "Read what an NPC says when you talk to them, above the conversation options.")
    Dropdown(voiced, "gossipRepeat", "Repeat greetings and gossip",
        "Whether an NPC's greeting and gossip are read again the next time you talk to them. \"Once\" is remembered on each character. Quest text is always read.",
        GOSSIP_REPEAT)

    -- Audio
    local audio = Settings.RegisterVerticalLayoutSubcategory(category, "Audio")
    local channels = {}
    for _, channel in ipairs(SOUND_CHANNELS) do
        table.insert(channels, { channel, channel })
    end
    Dropdown(audio, "soundChannel", "Sound channel", "Which volume slider controls the voiceovers.", channels)
    Dropdown(audio, "narratorVoice", "Narrator voice",
        "Some lines have no speaker to voice them: quests and chatter from objects, items and signs. A narrator reads those. Voice packs may carry them in other voices; a line the chosen voice does not have keeps the default narrator.",
        NarratorChoices, function(voice) ns.Packs:SetNarratorVoice(voice) end)
    Checkbox(audio, "muteGameDialog", "Mute the game's own NPC voices", "Turn off the Dialog channel while a voiceover plays so the two do not overlap.")
    Checkbox(audio, "stopOnClose", "Stop when the dialog closes", "Stop the current line when you close the quest or gossip window.")

    -- Talking head
    local display = Settings.RegisterVerticalLayoutSubcategory(category, "Talking head")
    Checkbox(display, "showPanel", "Show the panel", "Show the frame with the speaker, their name and the text while a line plays. Clear it for audio only.", RefreshHead)
    Checkbox(display, "showHead", "Show the talking head", "Show the speaker's portrait. Clear it to keep the parchment, the name and the text without the head.", RefreshHead)
    Checkbox(display, "showText", "Show the spoken text", "Display the words being spoken, paged in time with the audio.", RefreshHead)
    Checkbox(display, "lockHead", "Lock position", "Prevent the frame from being dragged.")
    Checkbox(display, "factionHead", "Faction parchment style", "Read quests on the light Alliance or Horde parchment. Clear it for Blizzard's dark talking head panel.", RefreshHead)
    Slider(display, "headScale", "Scale", "Size of the talking head.", 0.5, 1.5, 0.05, function(value) return format("%d%%", value * 100) end, RefreshHead)
    Checkbox(display, "showMinimapButton", "Minimap button", "Show the button on the minimap. Left-click for options, right-click for playback controls, drag to move.", function() ns.UI.MinimapButton:ApplySettings() end)
    Checkbox(display, "lockMinimapButton", "Lock minimap button", "Prevent the minimap button from being dragged.")

    -- Data
    local data = Settings.RegisterVerticalLayoutSubcategory(category, "Voice Pack Debug")
    Checkbox(data, "capture", "Record lines that have no audio", "Save every quest and gossip text you see so new voice lines can be generated from them.")
    Checkbox(data, "notifyUnvoiced", "Say when a line has no voice", "Print a chat line whenever a quest or gossip text has no audio yet. The line is saved for your next export either way.")
    Checkbox(data, "debug", "Debug messages", "Print matching details to chat.")
    Checkbox(data, "devOverlay", "Developer overlay",
        "Show a window attached to the quest or gossip frame with the quest file name, the voice, and the speaker's display and model ids. For people working on the voice pack.",
        function(value)
            pcall(C_CVar.SetCVar, "ForeverVO_devOverlay", value and "1" or "0")
            ns.UI.Debug:Apply()
        end)

    -- Last on the main page, below everything else
    layout:AddInitializer(VersionsInitializer())

    Settings.RegisterAddOnCategory(category)
end)
