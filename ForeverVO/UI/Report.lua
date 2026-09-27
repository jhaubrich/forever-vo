local _, ns = ...
local Util = ns.Util

--[[
Report copies a link that opens a GitHub issue about the line that is playing.
The link is the whole report: GitHub's new-issue URL takes the title and, for
a YAML form, one query parameter per field id (line, detail, versions, and
the rest). body= fills a blank issue, and this repo's forms would ignore it.
labels= is a permission the player does not have, and GitHub answers that
with a 404, so the form file's own labels are left to apply on their own.

The client cannot open a browser, and it blocks an addon that calls
CopyToClipboard (the same class of protected action as #44). Copy Link
selects the link so Ctrl+C takes it. The window is not a StaticPopup. With
the gamepad UI on, every StaticPopup is handed to a protected path and the
client freezes.

The spoken text is tokenised first. The talking head shows the line with the
character's name, class and race already written in, and those must not leave
in the link. The name passed to Tokenize is Util.Plain's, or "" when the
client has made it a secret: a nil argument makes Tokenize read UnitName
itself, which is not behind Plain and errors on a secret ("secret string
value").
]]

local Report = {}
ns.Report = Report

local BASE = "https://github.com/quinn-dougherty/forever-vo/issues/new"
-- Past this, GitHub returns 414 URI Too Long. The spoken text is the only
-- field that grows without a bound, so it is the one dropped.
local URL_BUDGET = 6000

local CHOICES = {
    { key = "voices", label = "It sounds wrong", template = "bug-voices.yml", title = "Voice" },
    { key = "captures", label = "The text or the speaker is wrong", template = "bug-captures.yml", title = "Capture" },
    { key = "playback", label = "The addon misbehaved", template = "bug-playback.yml", title = "Playback" },
}

local HINT = "Edit the report if you need to, then Copy Link and paste it into a browser. Choose what is wrong on the GitHub form, and submit.\nYour character name has been removed."
local PRESS_COPY = "Press Ctrl+C"
local PRESS_COPY_SHORT = "Press Ctrl+C. The link was shortened."

--- Percent-encode one query component. Unreserved characters stay as they
--- are; everything else, spaces included, is %XX per byte, so a multi-byte
--- character is encoded byte by byte and a space is %20.
local function Encode(value)
    return (tostring(value):gsub(".", function(char)
        local byte = char:byte()
        if (byte >= 48 and byte <= 57)
            or (byte >= 65 and byte <= 90)
            or (byte >= 97 and byte <= 122)
            or char == "-" or char == "_" or char == "." or char == "~" then
            return char
        end
        return format("%%%02X", byte)
    end))
end

local function Query(fields)
    local parts = {}
    for i, field in ipairs(fields) do
        parts[i] = Encode(field[1]) .. "=" .. Encode(field[2])
    end
    return BASE .. "?" .. table.concat(parts, "&")
end

--- Keeps an issue title inside a short length without cutting a multi-byte
--- character in half. Continuation bytes are 128-191; a leading byte is 192
--- or higher, and dropping it drops the whole character that would not fit.
local function TrimTitle(title)
    if #title <= 120 then
        return title
    end
    local cut = 117
    while cut > 0 and title:byte(cut) >= 128 and title:byte(cut) < 192 do
        cut = cut - 1
    end
    if cut > 0 and title:byte(cut) >= 192 then
        cut = cut - 1
    end
    if cut < 1 then
        return "..."
    end
    return title:sub(1, cut) .. "..."
end

local function OneLine(text)
    if not text or text == "" then
        return ""
    end
    return (text:gsub("%s+", " "))
end

--- Who is speaking and which line, on one line. Quest titles are quoted here;
--- a gossip option's title is already quoted by the event handler.
local function Identity(item)
    local who = item.name
    if not who or who == "" then
        who = "Unknown"
    end
    if item.kind == "quest" then
        local event = item.event or "?"
        local quest = item.questID and format("quest %s", item.questID) or "quest"
        if item.title and item.title ~= "" then
            return OneLine(format("%s, \"%s\" (%s), %s", who, item.title, quest, event))
        end
        return OneLine(format("%s, %s, %s", who, quest, event))
    end
    local event = item.event == "greeting" and "greeting" or (item.event or "gossip")
    if item.title and item.title ~= "" then
        return OneLine(format("%s, %s %s", who, event, item.title))
    end
    return OneLine(format("%s, %s", who, event))
end

local function IssueTitle(choice, item)
    local who = item.name
    if not who or who == "" then
        who = (item.kind == "quest" and item.questID) and format("quest %s", item.questID) or "line"
    end
    local extra = OneLine(item.title and item.title:gsub("\"", "") or "")
    local title = format("%s: %s", choice.title, who)
    if extra ~= "" then
        title = title .. ", " .. extra
    elseif item.event and item.event ~= "" then
        local event = item.event == "greeting" and "greeting" or item.event
        title = title .. ", " .. event
    end
    return TrimTitle(title)
end

local function ShortSound(path)
    if not path or path == "" then
        return nil
    end
    local short = path:match("[Ss]ounds\\(.+)$") or path:match("[Ss]ounds/(.+)$") or path
    short = short:gsub("\\", "/"):gsub("%.mp3$", "")
    return short
end

local function PackList()
    local names = {}
    for _, pack in ns.Packs:Iterate() do
        table.insert(names, pack.version and format("%s %s", pack.name, pack.version) or pack.name)
    end
    if #names == 0 then
        return "none"
    end
    return table.concat(names, ", ")
end

--- "m" or "f", or nil when the client will not say. UnitSex can come back a
--- secret, and comparing one errors, so this goes through Plain.
local function ReaderSex()
    local sex = Util.Plain(UnitSex("player"))
    if sex == 2 then
        return "m"
    elseif sex == 3 then
        return "f"
    end
end

local function SpokenText(item)
    local raw = Util.Plain(item.text)
    local text = Util.Tokenize(raw,
        Util.Plain(UnitName("player")) or "",
        Util.Plain(UnitClass("player")) or "",
        Util.Plain(UnitRace("player")) or "")
    return text or ""
end

local QUEST_VOICE = { accept = "va", progress = "vp", complete = "vc" }
local QUEST_PARTS = { accept = "aP", progress = "pP", complete = "cP" }

local function NarratorFolder(path)
    return path and path:match("\\Narrator\\([^\\]+)\\")
end

--- The archetype sound_index recorded for this file (scourge-male-dark), which
--- is also the clip's name. A narrator recording is not an archetype.
local function HeardVoice(item)
    if NarratorFolder(item.path) or item.voice == "narrator" then
        return nil
    end
    if item.voice then
        return item.voice
    end
    local pack = item.pack
    if item.kind ~= "quest" or not pack or not pack.quests or not item.questID then
        return nil
    end
    local key = QUEST_VOICE[item.event]
    local entry = key and pack.quests[item.questID]
    local voice = entry and entry[key]
    if voice and voice ~= "narrator" then
        return voice
    end
end

--- True when the narrator reads this line, or a stage direction inside it.
local function NarratorSpeaks(item)
    if NarratorFolder(item.path) or item.voice == "narrator" then
        return true
    end
    local pack = item.pack
    if item.kind == "quest" and pack and pack.quests and item.questID then
        local key = QUEST_PARTS[item.event]
        local parts = key and pack.quests[item.questID] and pack.quests[item.questID][key]
        if parts then
            for _, part in ipairs(parts) do
                if part.n then
                    return true
                end
            end
        end
    end
    if item.parts then
        for _, part in ipairs(item.parts) do
            if NarratorFolder(part.path) then
                return true
            end
        end
    end
end

local function Tail(item)
    local lines = {}
    -- The same words as the GitHub "Which line" box. That box is one line, so
    -- a long title looks cut off there; here it wraps.
    table.insert(lines, "Line: " .. Identity(item))
    local voice = HeardVoice(item)
    if voice then
        table.insert(lines, "Voice: " .. voice)
    end
    if NarratorSpeaks(item) then
        table.insert(lines, "Narrator: " .. ns.Packs.NarratorVoiceLabel(ns.Packs:NarratorVoice()))
    end
    local sound = ShortSound(item.path)
    if sound then
        table.insert(lines, "Sound: " .. sound)
    end
    if item.parts then
        local parts = {}
        for _, part in ipairs(item.parts) do
            local short = ShortSound(part.path)
            if short then
                table.insert(parts, short)
            end
        end
        if #parts > 0 then
            table.insert(lines, "Parts: " .. table.concat(parts, ", "))
        end
    end
    if item.pack and item.pack.name then
        local version = item.pack.version
        table.insert(lines, version and format("Pack: %s %s", item.pack.name, version) or ("Pack: " .. item.pack.name))
    end
    local sex = ReaderSex()
    if sex then
        table.insert(lines, "Reader sex: " .. sex)
    end
    return table.concat(lines, "\n")
end

--- The blank line after the prompt is where the cursor starts, so typing
--- stays above the sound and pack lines.
local NOTE_MARK = {
    voices = "Note:\n\n",
    captures = "What it should be:\n\n",
    playback = "Note:\n\n",
}

local function BodyFor(choice, item, text)
    local shown = text ~= "" and text or "(none)"
    local tail = Tail(item)
    local mark = NOTE_MARK[choice.key]
    local head
    if choice.key == "captures" then
        head = format("The talking head showed:\n\n%s\n\n%s\n%s", shown, mark, tail)
    elseif choice.key == "playback" then
        head = format("While this line was playing: %s\n\n%s\n\n%s\n%s", Identity(item), shown, mark, tail)
    else
        head = format("Spoken text:\n\n%s\n\n%s\n%s", shown, mark, tail)
    end
    return head
end

--- kind is left unset on the voice and capture forms. It is a required
--- dropdown whose option text would have to be copied here and matched
--- exactly, and the player picks it on the form. The editable box is the
--- textarea; the title, the line, and the versions stay beside it.
local function FieldsFromBody(choice, item, body)
    local identity = Identity(item)
    local title = IssueTitle(choice, item)
    if choice.key == "playback" then
        return {
            { "template", choice.template },
            { "title", title },
            { "what", body },
            { "packs", PackList() },
            { "version", ns.version or "dev" },
        }
    end
    local versions = choice.key == "voices"
        and format("addon %s; %s", ns.version or "dev", PackList())
        or format("addon %s", ns.version or "dev")
    local field = choice.key == "voices" and "detail" or "text"
    return {
        { "template", choice.template },
        { "title", title },
        { "line", identity },
        { field, body },
        { "versions", versions },
    }
end

--- Drops characters off the end of the body until the URL fits. The edit box
--- keeps the full text; only the link is shortened.
local function LinkFromBody(choice, item, body)
    local function urlFor(text)
        return Query(FieldsFromBody(choice, item, text))
    end
    local url = urlFor(body)
    if #url <= URL_BUDGET then
        return url, false
    end
    local suffix = "\n\n(shortened to fit the link)"
    local cut = #body
    while cut > 0 do
        while cut > 0 and body:byte(cut) >= 128 and body:byte(cut) < 192 do
            cut = cut - 1
        end
        if cut > 0 and body:byte(cut) >= 192 then
            cut = cut - 1
        end
        if cut < 0 then
            cut = 0
        end
        url = urlFor(body:sub(1, cut) .. suffix)
        if #url <= URL_BUDGET then
            return url, true
        end
        if cut == 0 then
            break
        end
        cut = math.max(cut - 200, 0)
    end
    return urlFor(suffix), true
end

function Report:Current()
    return ns.Queue:Current()
end

-- ---------------------------------------------------------------------------
-- Dialog
-- ---------------------------------------------------------------------------

local function CopyBox(parent, onEscape)
    local scroll = CreateFrame("ScrollFrame", nil, parent, "InputScrollFrameTemplate")
    scroll.EditBox:SetMaxLetters(0)
    scroll.EditBox:SetFontObject("GameFontHighlightSmall")
    scroll.EditBox:SetScript("OnEscapePressed", onEscape)
    if scroll.CharCount then
        scroll.CharCount:Hide()
    end
    return scroll
end

local function RadioLabel(button)
    -- The template's label is "text" or "Text", and a named frame also gets a
    -- global. Whichever is there is the label.
    local label = button.Text or button.text
    if not label then
        local name = button:GetName()
        label = name and _G[name .. "Text"]
    end
    if not label then
        label = button:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
        label:SetPoint("LEFT", button, "RIGHT", 4, 0)
        button.Text = label
    end
    return label
end

--- Replaces the scroll's anchors. SetPoint adds a point, so a second
--- BOTTOMRIGHT would stack on the first.
local function PlaceScroll(frame, bottom)
    local scroll = frame.Scroll
    scroll:ClearAllPoints()
    scroll:SetPoint("TOPLEFT", frame.Kinds[#CHOICES], "BOTTOMLEFT", 0, -12)
    scroll:SetPoint("BOTTOMRIGHT", -30, bottom)
end

function Report:GetFrame()
    if self.frame then
        return self.frame
    end
    local frame = CreateFrame("Frame", "ForeverVOReportFrame", UIParent, "ButtonFrameTemplate")
    self.frame = frame
    frame:SetSize(540, 470)
    frame:SetPoint("CENTER")
    frame:SetFrameStrata("DIALOG")
    frame:SetClampedToScreen(true)
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:SetTitle("Forever Voiceover: report this line")
    if ButtonFrameTemplate_HidePortrait then
        ButtonFrameTemplate_HidePortrait(frame)
    end
    tinsert(UISpecialFrames, "ForeverVOReportFrame")

    frame.Hint = frame:CreateFontString(nil, "ARTWORK")
    frame.Hint:SetFontObject("GameFontHighlight")
    frame.Hint:SetJustifyH("LEFT")
    frame.Hint:SetPoint("TOPLEFT", 16, -32)
    frame.Hint:SetPoint("RIGHT", -16, 0)
    frame.Hint:SetText(HINT)

    frame.Kinds = {}
    for index, choice in ipairs(CHOICES) do
        local radio = CreateFrame("CheckButton", "ForeverVOReportKind" .. index, frame, "UIRadioButtonTemplate")
        if index == 1 then
            radio:SetPoint("TOPLEFT", frame.Hint, "BOTTOMLEFT", 0, -10)
        else
            radio:SetPoint("TOPLEFT", frame.Kinds[index - 1], "BOTTOMLEFT", 0, -4)
        end
        local label = RadioLabel(radio)
        label:SetText(choice.label)
        radio:SetScript("OnClick", function()
            PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
            Report:SetKind(index)
        end)
        frame.Kinds[index] = radio
    end

    local function Hide()
        frame:Hide()
    end

    frame.Scroll = CopyBox(frame, Hide)
    PlaceScroll(frame, 46)
    frame.Scroll.EditBox:SetScript("OnTextChanged", function(_, userInput)
        if userInput then
            Report:ClearStatus()
        end
    end)

    -- The link, selected so Ctrl+C copies it. CopyToClipboard is protected.
    local link = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    link:SetAutoFocus(false)
    link:SetHeight(22)
    link:SetFontObject("GameFontHighlightSmall")
    link:SetMaxLetters(0)
    link:SetPoint("BOTTOMLEFT", 22, 40)
    link:SetPoint("BOTTOMRIGHT", -16, 40)
    link:SetScript("OnEscapePressed", Hide)
    link:SetScript("OnTextChanged", function(editBox, userInput)
        if userInput then
            editBox:SetText(frame.link or "")
            editBox:HighlightText()
        end
    end)
    link:SetScript("OnEditFocusGained", function(editBox)
        editBox:HighlightText()
    end)
    link:Hide()
    frame.LinkBox = link

    frame.CopyButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.CopyButton:SetSize(110, 22)
    frame.CopyButton:SetPoint("BOTTOMRIGHT", -16, 12)
    frame.CopyButton:SetText("Copy Link")
    frame.CopyButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Report:CopyLink()
    end)

    frame.Status = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
    frame.Status:SetPoint("RIGHT", frame.CopyButton, "LEFT", -12, 0)
    frame.Status:SetJustifyH("RIGHT")
    frame.Status:SetText("")
    return frame
end

function Report:ClearStatus()
    local frame = self.frame
    if not frame then
        return
    end
    if self.statusTimer then
        self.statusTimer:Cancel()
        self.statusTimer = nil
    end
    frame.Status:SetText("")
    if frame.LinkBox:IsShown() then
        frame.LinkBox:Hide()
        PlaceScroll(frame, 46)
    end
end

function Report:ShowStatus(text, r, g, b, hold)
    local frame = self.frame
    if self.statusTimer then
        self.statusTimer:Cancel()
        self.statusTimer = nil
    end
    frame.Status:SetText(text)
    frame.Status:SetTextColor(r, g, b)
    if not hold then
        self.statusTimer = C_Timer.NewTimer(4, function()
            if frame.Status:GetText() == text then
                frame.Status:SetText("")
            end
        end)
    end
end

function Report:SetKind(index)
    self.kind = index
    local frame = self:GetFrame()
    for i, radio in ipairs(frame.Kinds) do
        radio:SetChecked(i == index)
        local label = RadioLabel(radio)
        local width = label:GetStringWidth()
        if width and width > 0 then
            radio:SetHitRectInsets(0, -(width + 8), 0, 0)
        end
    end
    self:FillBody()
end

function Report:FillBody()
    local frame = self.frame
    local item = frame.item
    if not item then
        return
    end
    self:ClearStatus()
    local choice = CHOICES[self.kind or 1]
    local body = BodyFor(choice, item, SpokenText(item))
    local editBox = frame.Scroll.EditBox
    editBox:SetText(body)
    editBox:SetFocus()
    local mark = NOTE_MARK[choice.key]
    local at = mark and body:find(mark, 1, true)
    editBox:SetCursorPosition(at and (at + #mark - 1) or 0)
end

function Report:CopyLink()
    local frame = self.frame
    local item = frame.item
    if not item then
        return
    end
    local choice = CHOICES[self.kind or 1]
    local body = frame.Scroll.EditBox:GetText() or ""
    local url, shortened = LinkFromBody(choice, item, body)
    frame.link = url
    PlaceScroll(frame, 70)
    frame.LinkBox:Show()
    frame.LinkBox:SetText(url)
    frame.LinkBox:SetFocus()
    frame.LinkBox:HighlightText()
    self:ShowStatus(shortened and PRESS_COPY_SHORT or PRESS_COPY, 1, 0.82, 0, true)
end

function Report:Show(item)
    if not item then
        ns.Print("nothing is playing.")
        return
    end
    local frame = self:GetFrame()
    frame.item = item
    frame:Show()
    self:SetKind(1)
end
