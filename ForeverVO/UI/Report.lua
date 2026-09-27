local _, ns = ...
local Util = ns.Util

--[[
Report copies a link that opens a GitHub issue about the line that is playing.
The link is the whole report: GitHub's new-issue URL takes the title and, for
a YAML form, one query parameter per field id (line, detail, versions, and
the rest). body= fills a blank issue, and this repo's forms would ignore it.
labels= is a permission the player does not have, and GitHub answers that
with a 404, so the form file's own labels are left to apply on their own.

The client cannot open a browser. LaunchURL and CopyToClipboard are both
protected, and LaunchURL only accepts Blizzard's own sites, so this is the
same copy window as /fvo export: a highlighted box and Ctrl+C. It is not a
StaticPopup. With the gamepad UI on, every StaticPopup is handed to a
protected path and the client freezes (#44).

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

local HINT = "Press Ctrl+C to copy the link, then paste it into a browser. Choose what is wrong with the line on the GitHub form, and submit.\nYour character name has been removed."
local HINT_SHORT = "Press Ctrl+C to copy the link, then paste it into a browser. The link was shortened. The spoken text is in the box below; paste it into the details field on GitHub, choose what is wrong, and submit.\nYour character name has been removed."

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

local function Tail(item)
    local lines = {}
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
    table.insert(lines, "Narrator: " .. ns.Packs.NarratorVoiceLabel(ns.Packs:NarratorVoice()))
    local sex = ReaderSex()
    if sex then
        table.insert(lines, "Reader sex: " .. sex)
    end
    return table.concat(lines, "\n")
end

local function ShownText(text, includeText)
    if not includeText then
        return "Too long for this link. Paste it into this box from the other box in the copy window."
    end
    if text == "" then
        return "(none)"
    end
    return text
end

--- kind is left unset on the voice and capture forms. It is a required
--- dropdown whose option text would have to be copied here and matched
--- exactly, and the player picks it on the form.
local function Fields(choice, item, text, note, includeText)
    local identity = Identity(item)
    local title = IssueTitle(choice, item)
    local shown = ShownText(text, includeText)
    local tail = Tail(item)
    local body
    if choice.key == "captures" then
        body = format("The talking head showed:\n\n%s\n\nWhat it should be:\n\n%s\n\n%s",
            shown, note, tail)
    elseif choice.key == "playback" then
        body = format("While this line was playing: %s\n\n%s", identity, shown)
        if note ~= "" then
            body = body .. "\n\n" .. note
        end
        body = body .. "\n\n" .. tail
    else
        body = format("Spoken text:\n\n%s\n\nNote:\n\n%s\n\n%s",
            shown, note ~= "" and note or "(none)", tail)
    end
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

local function LinkFor(choice, item, text, note)
    local url = Query(Fields(choice, item, text, note, true))
    if #url <= URL_BUDGET then
        return url, nil
    end
    return Query(Fields(choice, item, text, note, false)), text
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
    scroll.EditBox:SetScript("OnEditFocusGained", function(editBox)
        editBox:HighlightText()
    end)
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

    frame.NoteLabel = frame:CreateFontString(nil, "ARTWORK")
    frame.NoteLabel:SetFontObject("GameFontNormal")
    frame.NoteLabel:SetJustifyH("LEFT")
    frame.NoteLabel:SetPoint("TOPLEFT", frame.Kinds[#CHOICES], "BOTTOMLEFT", 0, -12)
    frame.NoteLabel:SetText("Add a note (optional)")

    local note = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    note:SetAutoFocus(false)
    note:SetHeight(22)
    note:SetFontObject("GameFontHighlightSmall")
    note:SetMaxLetters(300)
    note:SetPoint("TOPLEFT", frame.NoteLabel, "BOTTOMLEFT", 6, -4)
    note:SetPoint("RIGHT", -20, 0)
    note:SetScript("OnEscapePressed", function() frame:Hide() end)
    note:SetScript("OnEnterPressed", function(editBox) editBox:ClearFocus() end)
    note:SetScript("OnTextChanged", function(_, userInput)
        if userInput then
            Report:Refresh(false)
        end
    end)
    frame.Note = note

    local function Hide()
        frame:Hide()
    end
    frame.Scroll = CopyBox(frame, Hide)
    frame.Scroll.EditBox:SetScript("OnTextChanged", function(editBox, userInput)
        if userInput then
            editBox:SetText(frame.link or "")
            editBox:HighlightText()
        end
    end)

    frame.OverflowLabel = frame:CreateFontString(nil, "ARTWORK")
    frame.OverflowLabel:SetFontObject("GameFontNormal")
    frame.OverflowLabel:SetJustifyH("LEFT")
    frame.OverflowLabel:SetText("Spoken text")

    frame.Overflow = CopyBox(frame, Hide)
    frame.Overflow.EditBox:SetScript("OnTextChanged", function(editBox, userInput)
        if userInput then
            editBox:SetText(frame.spoken or "")
            editBox:HighlightText()
        end
    end)
    frame.Overflow:Hide()
    frame.OverflowLabel:Hide()
    return frame
end

function Report:Layout(overflow)
    local frame = self.frame
    local scroll = frame.Scroll
    scroll:ClearAllPoints()
    scroll:SetPoint("TOPLEFT", frame.Note, "BOTTOMLEFT", -6, -10)
    scroll:SetPoint("RIGHT", -30, 0)
    if overflow then
        frame:SetHeight(640)
        frame.OverflowLabel:Show()
        frame.Overflow:Show()
        frame.OverflowLabel:ClearAllPoints()
        frame.OverflowLabel:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 16, 168)
        frame.Overflow:ClearAllPoints()
        frame.Overflow:SetPoint("TOPLEFT", frame.OverflowLabel, "BOTTOMLEFT", 0, -4)
        frame.Overflow:SetPoint("BOTTOMRIGHT", -30, 16)
        scroll:SetPoint("BOTTOM", frame.OverflowLabel, "TOP", 0, 8)
    else
        frame:SetHeight(470)
        frame.OverflowLabel:Hide()
        frame.Overflow:Hide()
        scroll:SetPoint("BOTTOM", frame, "BOTTOM", 0, 16)
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
    self:Refresh(true)
end

function Report:Refresh(focusLink)
    local frame = self.frame
    local item = frame.item
    if not item then
        return
    end
    local choice = CHOICES[self.kind or 1]
    local note = strtrim(frame.Note:GetText() or "")
    local text = SpokenText(item)
    local url, overflow = LinkFor(choice, item, text, note)
    frame.link = url
    frame.spoken = overflow
    frame.Hint:SetText(overflow and HINT_SHORT or HINT)
    self:Layout(overflow ~= nil)
    frame.Scroll.EditBox:SetText(url)
    if overflow then
        frame.Overflow.EditBox:SetText(overflow)
    end
    if focusLink then
        frame.Scroll.EditBox:SetFocus()
        frame.Scroll.EditBox:HighlightText()
    end
end

function Report:Show(item)
    if not item then
        ns.Print("nothing is playing.")
        return
    end
    local frame = self:GetFrame()
    frame.item = item
    frame.Note:SetText("")
    frame:Show()
    self:SetKind(1)
end
