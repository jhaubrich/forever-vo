local _, ns = ...
local Util = ns.Util

--[[
"/fvo export": packs the capture's unvoiced lines heard since the last export
(across sessions and characters now that the beta reads saved variables back)
into a string players can
paste into a GitHub issue (see .github/ISSUE_TEMPLATE/capture.yml). The
window shows that issue as text, and Copy Link builds the form URL with the
string in it, the same way /fvo report does. A GitHub link holds about 25
lines, so a longer export comes in parts, one issue each, every part a whole
export of its own (Export:Parts). All at Once is the other way: the link opens
the form without the string, and the player copies the string into it from a
box of its own, one issue for up to ~250 lines (the form's 65,536 characters). Copy Link
(or Ctrl+C in the text) stamps
ForeverVOCaptureDB.exportedAt past the parts copied so far (Export:MarkSent),
and Capture.Exported skips what an earlier export packed: before that, every export carried the
whole DB, and a player who exported after each quest, as the per-line
reminder suggested, sent the same hundred lines a hundred times. "/fvo export
all" packs everything again, for a link that was copied but never sent;
the lines stay in the DB either way. The string is JSON, zlib-compressed and
base64-encoded with the client's own
C_EncodingUtil, prefixed with "FVO1:". The character's name, class and race are
replaced by the $n/$c/$r placeholders (Util.Tokenize) at capture, so no line is
voiced for one class only, and the name never leaves the client. The class and
race do go along beside each line ("c", "r", since 0.1.7): the pipeline can
only tell a Mage's own "mage" from a literal one by comparing two readers it
knows to differ, and without them every community reader was a stranger. The
character's sex (one letter) goes along too: the client resolves a "$g lad:lass;"
branch before the addon sees a quest text, and the pipeline can only put the
branch back by comparing a male and a female reading, so a voiced line whose
pack still wants this sex's reading (Capture.Contributes) is packed too, as is
("v") a voiced quest line whose live text is not the one its pack voiced. Each
line carries when it was heard ("d", a timestamp) and the file carries the addon
version that heard it, so the pipeline can rank readings of the same line: a
newer addon's capture wins over an older one's, and among equals the more recent.
An NPC record goes along with its lines, and on its own when its model is not
the one the pack cast its voice from (`recast`) or it was met as a sex the
packs do not know it as (`newSex`, see Capture.lua).
tools/exportfile.py decodes it.
]]

local Export = {
    nudged = false,
}
ns.Export = Export

local NUDGE_AFTER = 10
local PREFIX = "FVO1:"
local ISSUE = "https://github.com/quinn-dougherty/forever-vo/issues/new"
-- GitHub fails a longer link: logged out, 6,500 characters redirected to the
-- login page and 7,000 got a 500 (8,200 and up a 414), measured 2026-09-30.
-- The export string is the field that grows, so a long export is split into
-- parts (Export:Parts) that fit.
local URL_BUDGET = 6000
-- A part is packed this far under the budget, so a note typed into it fits.
local NOTE_ROOM = 600
-- All at Once: the string is pasted into the form, whose box holds 65,536
-- characters (GitHub's issue body limit), so a part holds ~250-300 lines.
local PASTE_BUDGET = 60000
local NOTE = "## Note:\n\n"

--- Percent-encode one query component. Same rule as the report link. Only
--- the characters outside it reach the function: splitting an export encodes
--- a link's worth of text many times over.
local function Encode(value)
    return (tostring(value):gsub("[^0-9A-Za-z%-_%.~]", function(char)
        return format("%%%02X", char:byte())
    end))
end

local function Query(fields)
    local parts = {}
    for i, field in ipairs(fields) do
        parts[i] = Encode(field[1]) .. "=" .. Encode(field[2])
    end
    return ISSUE .. "?" .. table.concat(parts, "&")
end

local function NpcRecord(npc)
    return {
        name = npc.name, sex = npc.sex, displayID = npc.displayID,
        modelFileID = npc.modelFileID, creatureType = npc.creatureType, isObject = npc.isObject,
        addon = npc.addon, sexes = npc.sexes,
    }
end

--- Builds the export table from ForeverVOCaptureDB: lines without audio, and
--- voiced lines the pack asked to hear again from a reader like this one,
--- heard since the last export unless `all`. `npcs` holds the record of every
--- speaker a line names; `recast` the records that go along on their own
--- (a model or a sex the packs did not cast from).
function Export:Collect(all)
    local db = ForeverVOCaptureDB or {}
    local known = db.npcs or {}
    local lines, npcs, recast = {}, {}, {}
    local function add(kind, entry)
        if not ns.Capture.Contributes(entry) or (not all and ns.Capture.Exported(entry)) then
            return
        end
        table.insert(lines, {
            k = kind,
            e = entry.event,
            q = entry.questID,
            t = entry.title,
            p = entry.page,
            x = Util.Tokenize(entry.text, entry.player, entry.class, entry.race),
            n = entry.npc,
            s = entry.name,
            o = entry.isObject,
            z = entry.zone,
            m = entry.mapID,
            g = entry.sex,
            c = entry.class,
            r = entry.race,
            w = entry.wanted,
            v = entry.differs,
            d = entry.time,
        })
        if entry.npc and known[entry.npc] then
            npcs[entry.npc] = NpcRecord(known[entry.npc])
        end
    end
    for _, entry in pairs(db.quests or {}) do
        add("quest", entry)
    end
    for _, entry in pairs(db.gossip or {}) do
        add("gossip", entry)
    end
    for _, entry in pairs(db.books or {}) do
        add("book", entry)
    end
    local at = db.exportedAt
    for key, npc in pairs(known) do
        local flagged = math.max(npc.recast or 0, npc.newSex or 0)
        if flagged > 0 and (all or at == nil or flagged >= at) then
            recast[key] = NpcRecord(npc)
        end
    end
    return {
        addon = ns.version,
        build = select(2, GetBuildInfo()),
        locale = GetLocale(),
        lines = lines,
        npcs = npcs,
        recast = recast,
    }
end

local function EncodeData(part)
    local json = C_EncodingUtil.SerializeJSON({
        v = 1,
        addon = part.addon,
        build = part.build,
        locale = part.locale,
        lines = part.lines,
        npcs = part.npcs,
    })
    local compressed = C_EncodingUtil.CompressString(json, Enum.CompressionMethod.Zlib)
    return PREFIX .. C_EncodingUtil.EncodeBase64(compressed)
end

--- The lines of an export in parts, oldest first: Copy Link on a part
--- advances exportedAt past it (MarkSent), so a player who stops halfway gets
--- the rest at the next export. Each part is a whole export of its own, with
--- the records of its lines' speakers (and, in the first, the recast ones), so
--- the pipeline takes each issue as it is. In parts sized for a link, each
--- one's string rides in its URL; `paste` sizes them for the form's box
--- instead (All at Once), whose string the player pastes by hand. A line too
--- long for a link sits alone in a part marked `tooLong`.
---@param all boolean every line, exported before or not
---@param paste boolean parts sized for pasting, not for a link
function Export:Parts(all, paste)
    local data = self:Collect(all)
    local lines = data.lines
    table.sort(lines, function(a, b)
        return (a.d or 0) < (b.d or 0)
    end)
    local parts = {}
    local function Slice(from, to)
        local part = { addon = data.addon, build = data.build, locale = data.locale,
            lines = {}, npcs = {} }
        if #parts == 0 then
            for key, record in pairs(data.recast) do
                part.npcs[key] = record
            end
        end
        for i = from, to do
            local line = lines[i]
            table.insert(part.lines, line)
            if line.n and data.npcs[line.n] then
                part.npcs[line.n] = data.npcs[line.n]
            end
        end
        return part
    end
    local function Fits(part)
        local payload = EncodeData(part)
        if paste then
            return #payload <= PASTE_BUDGET, payload
        end
        -- The widest part number stands in for the real one, not known yet.
        local url, shortened = self:Link(self:Body(part, payload, 99, 99), part, 99, 99)
        return not shortened and #url <= URL_BUDGET - NOTE_ROOM, payload
    end
    -- The longest run from `first` that fits: double the run until it does
    -- not, then bisect. A few encodes per part, each about a part's size,
    -- rather than one per line or one of everything that is left.
    local first = 1
    while first <= #lines do
        local good, bad = first, nil
        local step = 1
        while not bad and good < #lines do
            local try = math.min(first + step, #lines)
            if Fits(Slice(first, try)) then
                good = try
                step = step * 2
            else
                bad = try
            end
        end
        while bad and bad - good > 1 do
            local mid = math.floor((good + bad) / 2)
            if Fits(Slice(first, mid)) then
                good = mid
            else
                bad = mid
            end
        end
        local part = Slice(first, good)
        local fits, payload = Fits(part)
        part.payload = payload
        part.tooLong = not fits
        table.insert(parts, part)
        first = good + 1
    end
    return parts
end

--- The issue text. The note is where the cursor starts. The facts under it
--- are labels, and the export string stays whole at the bottom so a note
--- does not land in the middle of it.
function Export:Body(part, payload, index, total, note)
    local count = #part.lines
    local summary = format("%d %s%s. Your character name has been removed.",
        count, Util.Plural(count, "line"),
        total > 1 and format(" (part %d of %d)", index, total) or "")
    local facts = { "Lines: " .. count, "Addon: " .. (part.addon or "dev") }
    if part.build and part.build ~= "" then
        table.insert(facts, "Build: " .. tostring(part.build))
    end
    return format("## Captured lines\n\n%s\n\n%s%s\n\n%s\n\n## Export string\n\n%s",
        summary, NOTE, note or "", table.concat(facts, "\n"), payload)
end

--- What the player typed under the note heading, to carry into the next part.
local function NoteOf(body)
    local note = body:match("## Note:\n\n(.-)\n\nLines: ")
    return note and note:match("^%s*(.-)%s*$") or ""
end

local function Title(part, index, total)
    local count = #part.lines
    return total > 1
        and format("Captured lines (%d, part %d of %d)", count, index, total)
        or format("Captured lines (%d)", count)
end

--- The capture form's export field is the whole text, and the decoder finds
--- the FVO1 string inside it. A string that will not fit in the URL is left
--- out; the form still opens on the right template.
function Export:Link(body, part, index, total)
    local title = Title(part, index, total)
    local url = Query({
        { "template", "capture.yml" },
        { "title", title },
        { "export", body },
    })
    if #url <= URL_BUDGET then
        return url, false
    end
    return Query({
        { "template", "capture.yml" },
        { "title", title },
    }), true
end

--- All at Once: the link opens the form with the title, and the note in its
--- "Anything else" field; the player pastes the string into Export string.
function Export:PasteLink(part, index, total, note)
    local fields = {
        { "template", "capture.yml" },
        { "title", Title(part, index, total) },
    }
    if note ~= "" then
        table.insert(fields, { "notes", note })
    end
    return Query(fields)
end

-- ---------------------------------------------------------------------------
-- Dialog
-- ---------------------------------------------------------------------------

local function PlaceScroll(frame, bottom)
    local scroll = frame.Scroll
    scroll:ClearAllPoints()
    scroll:SetPoint("TOPLEFT", frame.Hint, "BOTTOMLEFT", -4, -16)
    scroll:SetPoint("BOTTOMRIGHT", -30, bottom)
end

function Export:GetFrame()
    if self.frame then
        return self.frame
    end
    local frame = CreateFrame("Frame", "ForeverVOExportFrame", UIParent, "ButtonFrameTemplate")
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
    frame:SetTitle("Forever Voiceover: contribute lines")
    if ButtonFrameTemplate_HidePortrait then
        ButtonFrameTemplate_HidePortrait(frame)
    end
    tinsert(UISpecialFrames, "ForeverVOExportFrame") -- close with Escape

    frame.Hint = frame:CreateFontString(nil, "ARTWORK")
    frame.Hint:SetFontObject("GameFontHighlight")
    frame.Hint:SetJustifyH("LEFT")
    frame.Hint:SetSpacing(3)
    frame.Hint:SetPoint("TOPLEFT", 20, -38)
    frame.Hint:SetPoint("RIGHT", -20, 0)
    frame.Hint:SetText("Add a note if you like, then click Copy Link and paste it into your browser. It opens a GitHub form with all of this filled in.\n\nOne export covers everything you have seen since the last one, so there is no need to do this after every quest. Closed without copying, the lines wait for the next one.")

    local function Hide()
        frame:Hide()
    end
    -- Only a copy counts a part as sent (MarkSent), and before 0.1.8 merely
    -- opening the window did, so say what closing it kept (#1199).
    frame:SetScript("OnHide", function()
        local waiting = 0
        for i, part in ipairs(frame.parts or {}) do
            if not frame.copied[i] then
                waiting = waiting + #part.lines
            end
        end
        if waiting > 0 then
            ns.Print(format("%d %s not copied, kept for your next |cffffd100/fvo export|r.",
                waiting, Util.Plural(waiting, "line")))
        end
    end)

    local scroll = CreateFrame("ScrollFrame", nil, frame, "InputScrollFrameTemplate")
    scroll.EditBox:SetMaxLetters(0)
    scroll.EditBox:SetFontObject("GameFontHighlightSmall")
    scroll.EditBox:SetScript("OnEscapePressed", Hide)
    -- Copying the issue text by hand, to paste into the form or the inbox
    -- issue, sends the part as surely as Copy Link does (#1199).
    scroll.EditBox:SetScript("OnKeyDown", function(_, key)
        if key == "C" and (IsControlKeyDown() or IsMetaKeyDown()) and frame.index then
            Export:MarkSent(frame.index)
        end
    end)
    scroll.EditBox:SetScript("OnTextChanged", function(_, userInput)
        if userInput then
            Export:ClearStatus()
        end
    end)
    if scroll.CharCount then
        scroll.CharCount:Hide()
    end
    frame.Scroll = scroll
    PlaceScroll(frame, 46)

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

    -- Between the text and the link, the full width of the window, one line:
    -- beside the buttons it ran off the left edge.
    frame.Status = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
    frame.Status:SetPoint("BOTTOMLEFT", link, "TOPLEFT", -4, 6)
    frame.Status:SetPoint("RIGHT", -16, 0)
    frame.Status:SetJustifyH("LEFT")
    frame.Status:SetWordWrap(false)
    frame.Status:Hide()

    -- All at Once: the export string on its own, to copy into the form, with
    -- its step above it; the link and its step move up to make room.
    local paste = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    paste:SetAutoFocus(false)
    paste:SetHeight(22)
    paste:SetFontObject("GameFontHighlightSmall")
    paste:SetMaxLetters(0)
    paste:SetPoint("BOTTOMLEFT", 22, 40)
    paste:SetPoint("BOTTOMRIGHT", -16, 40)
    paste:SetScript("OnEscapePressed", Hide)
    paste:SetScript("OnTextChanged", function(editBox, userInput)
        if userInput then
            editBox:SetText(frame.pasteText or "")
            editBox:HighlightText()
        end
    end)
    paste:SetScript("OnEditFocusGained", function(editBox)
        editBox:HighlightText()
    end)
    paste:Hide()
    frame.PasteBox = paste

    frame.PasteStatus = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
    frame.PasteStatus:SetPoint("BOTTOMLEFT", paste, "TOPLEFT", -4, 6)
    frame.PasteStatus:SetPoint("RIGHT", -16, 0)
    frame.PasteStatus:SetJustifyH("LEFT")
    frame.PasteStatus:SetWordWrap(false)
    frame.PasteStatus:SetTextColor(1, 0.82, 0)
    frame.PasteStatus:Hide()

    -- The buttons sit in the template's button bar, the 26 px under its
    -- inset, where Blizzard's own windows put theirs (MagicButton_OnLoad's
    -- offsets: 4 from the bottom, 4 from the left edge, 6 from the right, and
    -- neighbours touching). Higher up they straddled the inset's border.
    frame.CopyButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.CopyButton:SetSize(110, 22)
    frame.CopyButton:SetPoint("BOTTOMRIGHT", -6, 4)
    frame.CopyButton:SetText("Copy Link")
    frame.CopyButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Export:CopyLink()
    end)

    frame.NextButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.NextButton:SetSize(80, 22)
    frame.NextButton:SetPoint("RIGHT", frame.CopyButton, "LEFT", -1, 0)
    frame.NextButton:SetText("Next")
    frame.NextButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Export:ShowPart(frame.index + 1)
    end)

    frame.PreviousButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.PreviousButton:SetSize(80, 22)
    frame.PreviousButton:SetPoint("RIGHT", frame.NextButton, "LEFT", -1, 0)
    frame.PreviousButton:SetText("Previous")
    frame.PreviousButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Export:ShowPart(frame.index - 1)
    end)

    -- One issue however much was captured, by pasting instead of a link.
    frame.ModeButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.ModeButton:SetSize(110, 22)
    frame.ModeButton:SetPoint("BOTTOMLEFT", 4, 4)
    frame.ModeButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Export:SetMode(not frame.paste)
    end)
    frame.ModeButton:SetScript("OnEnter", function(button)
        GameTooltip:SetOwner(button, "ANCHOR_TOP")
        if frame.paste then
            GameTooltip:SetText("In Parts", 1, 0.82, 0)
            GameTooltip:AddLine("One link per part, with everything filled in.", 1, 1, 1, true)
        else
            GameTooltip:SetText("All at Once", 1, 0.82, 0)
            GameTooltip:AddLine("Send everything in one issue: the link opens the form, and you copy the export string into it yourself.", 1, 1, 1, true)
        end
        GameTooltip:Show()
    end)
    frame.ModeButton:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)

    frame.PartLabel = frame:CreateFontString(nil, "ARTWORK", "GameFontNormal")
    frame.PartLabel:SetPoint("LEFT", frame.ModeButton, "RIGHT", 4, 0)
    frame.PartLabel:SetPoint("RIGHT", frame.PreviousButton, "LEFT", -4, 0)
    frame.PartLabel:SetJustifyH("CENTER")
    frame.PartLabel:SetWordWrap(false)
    return frame
end

function Export:ClearStatus()
    local frame = self.frame
    if not frame then
        return
    end
    frame.Status:Hide()
    frame.PasteStatus:Hide()
    frame.PasteBox:Hide()
    if frame.LinkBox:IsShown() then
        frame.LinkBox:Hide()
        frame.LinkBox:SetPoint("BOTTOMLEFT", 22, 40)
        frame.LinkBox:SetPoint("BOTTOMRIGHT", -16, 40)
        PlaceScroll(frame, 46)
    end
end

--- Switches between parts sized for a link and All at Once, rebuilding the
--- parts; the note carries over. What was copied so far stays counted in
--- exportedAt, and the new parts start from nothing copied, which can only
--- re-send a line, never skip one.
function Export:SetMode(paste)
    local frame = self.frame
    frame.paste = paste
    frame.parts = self:Parts(frame.all, paste)
    frame.copied = {}
    self:ShowPart(1)
end

--- Puts part `index` in the window, carrying over the note typed so far.
function Export:ShowPart(index)
    local frame = self.frame
    local parts = frame.parts
    if not parts[index] then
        return
    end
    local editBox = frame.Scroll.EditBox
    local note = frame.index and NoteOf(editBox:GetText() or "") or ""
    frame.index = index
    local part = parts[index]
    self:ClearStatus()
    local body = self:Body(part, part.payload, index, #parts, note)
    editBox:SetText(body)
    local at = body:find(NOTE, 1, true)
    editBox:SetCursorPosition(at and (at + #NOTE - 1 + #note) or 0)

    local split = #parts > 1
    frame.PartLabel:SetText(split and format("Part %d of %d", index, #parts) or "")
    frame.PreviousButton:SetShown(split)
    frame.NextButton:SetShown(split)
    frame.PreviousButton:SetEnabled(index > 1)
    frame.NextButton:SetEnabled(index < #parts)
    -- Offered only where it changes something: more than one link's worth.
    frame.ModeButton:SetText(frame.paste and "In Parts" or "All at Once")
    frame.ModeButton:SetShown(frame.paste or split)
end

--- Counts parts 1..k as sent once every one of them has had its link copied:
--- exportedAt moves to the first line of the first part not copied yet (lines
--- are oldest first), or to when the window opened once all are. It never
--- moves back, so an "export all" or a second copy leaves it alone.
function Export:MarkSent(index)
    local frame = self.frame
    local db = ForeverVOCaptureDB
    if not db then
        return
    end
    frame.copied[index] = true
    local at = frame.shownAt
    for i, part in ipairs(frame.parts) do
        if not frame.copied[i] then
            at = part.lines[1].d or 0
            break
        end
    end
    if (db.exportedAt or 0) < at then
        db.exportedAt = at
    end
end

function Export:CopyLink()
    local frame = self.frame
    if frame.paste then
        return self:CopyPaste()
    end
    local part = frame.parts[frame.index]
    local body = frame.Scroll.EditBox:GetText() or ""
    if part.tooLong then
        ns.UI.Alert:Show("This line is too long to fit in a link. Click All at Once to send it by pasting instead.")
        return
    end
    local url, shortened = self:Link(body, part, frame.index, #frame.parts)
    if shortened then
        ns.UI.Alert:Show("Your note is too long for the link. Shorten it and click Copy Link again.")
        return
    end
    frame.link = url
    -- No SetFocus here. A button click that takes keyboard focus is the
    -- protected call the client reports as UNKNOWN(). Clicking the link is
    -- what focuses it, and OnEditFocusGained selects the text.
    PlaceScroll(frame, 94)
    frame.LinkBox:Show()
    frame.LinkBox:SetText(url)
    local more = frame.index < #frame.parts and " Then click Next." or ""
    frame.Status:SetText("Click the link, press Ctrl+C, and paste it into your browser." .. more)
    frame.Status:SetTextColor(1, 0.82, 0)
    frame.Status:Show()
    self:MarkSent(frame.index)
    ns.Print("thank you for helping voice Forever! Once the form is submitted, your lines go into the next voice pack.")
end

--- All at Once: a link to the form (title and note) and the string to paste.
function Export:CopyPaste()
    local frame = self.frame
    local part = frame.parts[frame.index]
    local note = NoteOf(frame.Scroll.EditBox:GetText() or "")
    local url = self:PasteLink(part, frame.index, #frame.parts, note)
    if #url > URL_BUDGET then
        ns.UI.Alert:Show("Your note is too long for the link. Shorten it and click Copy Link again.")
        return
    end
    frame.link = url
    frame.pasteText = part.payload
    PlaceScroll(frame, 142)
    frame.LinkBox:SetPoint("BOTTOMLEFT", 22, 88)
    frame.LinkBox:SetPoint("BOTTOMRIGHT", -16, 88)
    frame.LinkBox:Show()
    frame.LinkBox:SetText(url)
    frame.Status:SetText("1. Click the link, press Ctrl+C, and open it in your browser.")
    frame.Status:SetTextColor(1, 0.82, 0)
    frame.Status:Show()
    frame.PasteBox:Show()
    frame.PasteBox:SetText(part.payload)
    frame.PasteBox:SetCursorPosition(0)
    local more = frame.index < #frame.parts and " Then Next." or ""
    frame.PasteStatus:SetText("2. Click here, press Ctrl+C, and paste it into Export string." .. more)
    frame.PasteStatus:Show()
    self:MarkSent(frame.index)
    ns.Print("thank you for helping voice Forever! Once the form is submitted, your lines go into the next voice pack.")
end

--- How many lines an export would carry now, counted as the login window
--- counts them: distinct quests (an offer and its turn-in are one), gossip and
--- pages of books.
function Export:PendingCount()
    if not Util.EnglishClient() then
        return 0  -- nothing a client in another language holds is sent
    end
    local quests, gossip, books = ns.Capture:Pending()
    return quests + gossip + books
end

--- "Send 12 Quests to Project", or the plain label when there is nothing to
--- send; the options button and the minimap menu both use it.
function Export:SendLabel()
    local count = self:PendingCount()
    if count == 0 then
        return "Send Quests to Project"
    end
    return format("Send %d %s to Project", count, Util.Plural(count, "Quest"))
end

--- Whether Show(all) would open the window rather than only print to chat.
---@param all boolean
function Export:HasLines(all)
    return #self:Collect(all).lines > 0
end

---@param all boolean pack every line, exported before or not
function Export:Show(all)
    local db = ForeverVOCaptureDB or {}
    if not Util.EnglishClient() then
        ns.UI.Alert:Show("Only English game clients can send lines for now: the voice packs are English, and lines from another language would replace them.")
        return
    end
    local parts = self:Parts(all)
    if #parts == 0 then
        if not all and db.exportedAt then
            ns.UI.Alert:Show("Nothing new since your last export.\n\n|cffffd100/fvo export all|r packs everything again.")
        else
            ns.UI.Alert:Show("Nothing to export: every line seen so far already has audio.")
        end
        return
    end
    local frame = self:GetFrame()
    frame.all = all
    frame.paste = false
    frame.parts = parts
    frame.copied = {}
    frame.shownAt = time()
    frame.index = nil
    self:ShowPart(1)
    frame:Show()
    frame.Scroll.EditBox:SetFocus()
    local count = 0
    for _, part in ipairs(parts) do
        count = count + #part.lines
    end
    local since = (not all and db.exportedAt) and " heard since your last export" or ""
    local split = #parts > 1
        and format(" in %d parts, one issue each (a link only holds so much; All at Once sends one)", #parts) or ""
    ns.Print(format("%d %s%s packed%s. You do not have to do this for each quest.",
        count, Util.Plural(count, "line"), since, split))
end

--- Called by Capture after each recorded line; reminds the player once per session.
---@param contributes boolean whether an export would carry the line
function Export:OnLineCaptured(contributes)
    if not contributes or self.nudged then
        return
    end
    self.count = (self.count or 0) + 1
    if self.count >= NUDGE_AFTER then
        self.nudged = true
        ns.Print(format("%d lines seen this session are worth contributing. |cffffd100/fvo export|r packs them whenever you like; there is no need to do it for each quest.", self.count))
    end
end

-- Reminder when a logout or quit timer starts. The capture persists between
-- sessions, so nothing is lost by leaving; the count says what this session
-- added and what was already waiting from earlier ones.
ns.OnInit(function()
    local frame = CreateFrame("Frame")
    frame:RegisterEvent("PLAYER_CAMPING")
    frame:RegisterEvent("PLAYER_QUITING")
    frame:SetScript("OnEvent", function()
        local _, questsMissing, _, gossipMissing, _, sessionMissing, _, booksMissing = ns.Capture:Summary()
        local missing = questsMissing + gossipMissing + booksMissing
        if missing == 0 then
            return
        end
        local earlier = missing - sessionMissing
        if earlier > 0 and sessionMissing > 0 then
            ns.Print(format("%d %s from this session and %d from earlier ones are worth contributing. |cffffd100/fvo export|r packs them all; they keep until you do.",
                sessionMissing, Util.Plural(sessionMissing, "line"), earlier))
        elseif sessionMissing > 0 then
            ns.Print(format("%d %s from this session are worth contributing. |cffffd100/fvo export|r packs them; they keep until you do.",
                sessionMissing, Util.Plural(sessionMissing, "line")))
        else
            ns.Print(format("%d %s from earlier sessions are still worth contributing. |cffffd100/fvo export|r packs them.",
                earlier, Util.Plural(earlier, "line")))
        end
    end)
end)
