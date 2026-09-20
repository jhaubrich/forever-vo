local _, ns = ...

--[[
Options panel built on the Settings API (Escape > Options > AddOns).
Boolean settings write straight into ns.db; dropdowns and the slider go through
proxy settings so the saved values stay readable strings/numbers.
]]

local SettingsPanel = {}
ns.SettingsPanel = SettingsPanel

local GOSSIP_FREQUENCIES = {
    { "always",          "Every time" },
    { "oncePerQuestNPC", "Once per NPC that offers quests" },
    { "oncePerNPC",      "Once per NPC" },
    { "never",           "Never" },
}
local SOUND_CHANNELS = { "Master", "Dialog", "SFX", "Music", "Ambience" }

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
    -- choices: list of { value, label }; stored value is choices[i][1]
    local function GetValue()
        for index, choice in ipairs(choices) do
            if choice[1] == ns.db[key] then
                return index
            end
        end
        return 1
    end
    local function SetValue(index)
        ns.db[key] = choices[index][1]
        if onChange then
            onChange(ns.db[key])
        end
    end
    local function GetOptions()
        local container = Settings.CreateControlTextContainer()
        for index, choice in ipairs(choices) do
            container:Add(index, choice[2])
        end
        return container:GetData()
    end
    local default = 1
    for index, choice in ipairs(choices) do
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

function SettingsPanel:Open()
    if self.category then
        Settings.OpenToCategory(self.category:GetID())
    end
end

ns.OnInit(function()
    local category = Settings.RegisterVerticalLayoutCategory("Forever Voiceover")
    SettingsPanel.category = category
    local head = ns.UI.TalkingHead

    local function RefreshHead()
        head:ApplySettings()
    end

    -- What to voice
    local voiced = Settings.RegisterVerticalLayoutSubcategory(category, "What to voice")
    Checkbox(voiced, "playAccept", "Quest offers", "Read the quest text when a quest is offered.")
    Checkbox(voiced, "playComplete", "Quest turn-ins", "Read the reward text when handing in a quest.")
    Checkbox(voiced, "playProgress", "Quest progress", "Read the 'not yet complete' text when returning early. Usually best left off.")
    Checkbox(voiced, "playGreeting", "Greetings", "Read the greeting of NPCs that offer quests.")
    Checkbox(voiced, "playGossip", "Gossip", "Read NPC conversation text.")
    Dropdown(voiced, "gossipFrequency", "Repeat gossip", "How often the same NPC's gossip is read again.", GOSSIP_FREQUENCIES)

    -- Audio
    local audio = Settings.RegisterVerticalLayoutSubcategory(category, "Audio")
    local channels = {}
    for _, channel in ipairs(SOUND_CHANNELS) do
        table.insert(channels, { channel, channel })
    end
    Dropdown(audio, "soundChannel", "Sound channel", "Which volume slider controls the voiceovers.", channels)
    Checkbox(audio, "muteGameDialog", "Mute the game's own NPC voices", "Turn off the Dialog channel while a voiceover plays so the two do not overlap.")
    Checkbox(audio, "stopOnClose", "Stop when the dialog closes", "Stop the current line when you close the quest or gossip window.")

    -- Talking head
    local display = Settings.RegisterVerticalLayoutSubcategory(category, "Talking head")
    Checkbox(display, "showHead", "Show the talking head", "Show the speaker, their name and the text while a line plays.", RefreshHead)
    Checkbox(display, "showText", "Show the spoken text", "Display the words being spoken, paged in time with the audio.", RefreshHead)
    Checkbox(display, "lockHead", "Lock position", "Prevent the frame from being dragged.")
    Slider(display, "headScale", "Scale", "Size of the talking head.", 0.5, 1.5, 0.05, function(value) return format("%d%%", value * 100) end, RefreshHead)
    Checkbox(display, "showMinimapButton", "Minimap button", "Show the button on the minimap. Left-click for options, right-click for playback controls, drag to move.", function() ns.UI.MinimapButton:ApplySettings() end)
    Checkbox(display, "lockMinimapButton", "Lock minimap button", "Prevent the minimap button from being dragged.")

    -- Data
    local data = Settings.RegisterVerticalLayoutSubcategory(category, "Voice packs")
    Checkbox(data, "capture", "Record lines that have no audio", "Save every quest and gossip text you see so new voice lines can be generated from them.")
    Checkbox(data, "notifyUnvoiced", "Say when a line has no voice", "Print a chat line, with the export reminder, whenever a quest or gossip text has no audio yet.")
    Checkbox(data, "debug", "Debug messages", "Print matching details to chat.")

    Settings.RegisterAddOnCategory(category)
end)
