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
export of its own (Export:Parts). Copy Link stamps
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
the one the pack cast its voice from (`recast`, see Capture.lua).
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
local NOTE = "## Note:\n\n"

--- Percent-encode one query component. Same rule as the report link.
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
--- speaker a line names; `recast` the records that go along on their own.
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
    local at = db.exportedAt
    for key, npc in pairs(known) do
        if npc.recast and (all or at == nil or npc.recast >= at) then
            recast[key] = NpcRecord(npc)
        end
    end
    return {
        addon = ns.version,
        build = select(2, GetBuildInfo()),
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
        lines = part.lines,
        npcs = part.npcs,
    })
    local compressed = C_EncodingUtil.CompressString(json, Enum.CompressionMethod.Zlib)
    return PREFIX .. C_EncodingUtil.EncodeBase64(compressed)
end

--- The lines of an export in parts whose links each fit GitHub's URL limit,
--- oldest first: Copy Link on a part advances exportedAt past it (MarkSent),
--- so a player who stops halfway gets the rest at the next export. Each part is
--- a whole export of its own, with the records of its lines' speakers (and, in
--- the first, the recast ones), so the pipeline takes each issue as it is.
--- A line too long for any link sits alone in a part marked `tooLong`.
function Export:Parts(all)
    local data = self:Collect(all)
    table.sort(data.lines, function(a, b)
        return (a.d or 0) < (b.d or 0)
    end)
    local parts = {}
    local function NewPart()
        local part = { addon = data.addon, build = data.build, lines = {}, npcs = {} }
        if #parts == 0 then
            for key, record in pairs(data.recast) do
                part.npcs[key] = record
            end
        end
        return part
    end
    local function Fits(part)
        -- The widest part number stands in for the real one, not known yet.
        local url, shortened = self:Link(self:Body(part, EncodeData(part), 99, 99), part, 99, 99)
        return not shortened and #url <= URL_BUDGET - NOTE_ROOM
    end
    local part = NewPart()
    for _, line in ipairs(data.lines) do
        table.insert(part.lines, line)
        local added = line.n and not part.npcs[line.n] and data.npcs[line.n]
        if added then
            part.npcs[line.n] = added
        end
        if #part.lines > 1 and not Fits(part) then
            table.remove(part.lines)
            if added then
                part.npcs[line.n] = nil
            end
            table.insert(parts, part)
            part = NewPart()
            table.insert(part.lines, line)
            if line.n and data.npcs[line.n] then
                part.npcs[line.n] = data.npcs[line.n]
            end
        end
    end
    if #part.lines > 0 then
        table.insert(parts, part)
    end
    for _, each in ipairs(parts) do
        each.payload = EncodeData(each)
        each.tooLong = #each.lines == 1 and not Fits(each)
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

--- The capture form's export field is the whole text, and the decoder finds
--- the FVO1 string inside it. A string that will not fit in the URL is left
--- out; the form still opens on the right template.
function Export:Link(body, part, index, total)
    local count = #part.lines
    local title = total > 1
        and format("Captured lines (%d, part %d of %d)", count, index, total)
        or format("Captured lines (%d)", count)
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

-- ---------------------------------------------------------------------------
-- Dialog
-- ---------------------------------------------------------------------------

local function PlaceScroll(frame, bottom)
    local scroll = frame.Scroll
    scroll:ClearAllPoints()
    scroll:SetPoint("TOPLEFT", frame.Hint, "BOTTOMLEFT", 0, -12)
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
    frame.Hint:SetPoint("TOPLEFT", 16, -32)
    frame.Hint:SetPoint("RIGHT", -16, 0)
    frame.Hint:SetText("Edit the note if you need to, then Copy Link and paste it into a browser. The form is Contribute captured lines.\nYou do not have to do this for each quest: one export packs up everything you have seen since the last one.\nYour character name has been removed.")

    local function Hide()
        frame:Hide()
    end

    local scroll = CreateFrame("ScrollFrame", nil, frame, "InputScrollFrameTemplate")
    scroll.EditBox:SetMaxLetters(0)
    scroll.EditBox:SetFontObject("GameFontHighlightSmall")
    scroll.EditBox:SetScript("OnEscapePressed", Hide)
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

    frame.CopyButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.CopyButton:SetSize(110, 22)
    frame.CopyButton:SetPoint("BOTTOMRIGHT", -16, 12)
    frame.CopyButton:SetText("Copy Link")
    frame.CopyButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Export:CopyLink()
    end)

    frame.NextButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.NextButton:SetSize(90, 22)
    frame.NextButton:SetPoint("RIGHT", frame.CopyButton, "LEFT", -8, 0)
    frame.NextButton:SetText("Next")
    frame.NextButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Export:ShowPart(frame.index + 1)
    end)

    frame.PreviousButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.PreviousButton:SetSize(90, 22)
    frame.PreviousButton:SetPoint("RIGHT", frame.NextButton, "LEFT", -4, 0)
    frame.PreviousButton:SetText("Previous")
    frame.PreviousButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Export:ShowPart(frame.index - 1)
    end)

    frame.PartLabel = frame:CreateFontString(nil, "ARTWORK", "GameFontNormal")
    frame.PartLabel:SetPoint("LEFT", frame, "BOTTOMLEFT", 20, 23)
    frame.PartLabel:SetPoint("RIGHT", frame.PreviousButton, "LEFT", -8, 0)
    frame.PartLabel:SetJustifyH("LEFT")
    frame.PartLabel:SetWordWrap(false)
    return frame
end

function Export:ClearStatus()
    local frame = self.frame
    if not frame then
        return
    end
    frame.Status:Hide()
    if frame.LinkBox:IsShown() then
        frame.LinkBox:Hide()
        PlaceScroll(frame, 46)
    end
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
    local part = frame.parts[frame.index]
    local body = frame.Scroll.EditBox:GetText() or ""
    local url, shortened = self:Link(body, part, frame.index, #frame.parts)
    if shortened and not part.tooLong then
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
    if part.tooLong then
        ns.UI.Alert:Show("This line is too long to fit in a link, so the link opens the form empty. Click in the text above, press Ctrl+A and Ctrl+C, and paste it into the form's Export string box.")
    end
end

---@param all boolean pack every line, exported before or not
function Export:Show(all)
    local db = ForeverVOCaptureDB or {}
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
        and format(" in %d parts, one issue each (a link only holds so much)", #parts) or ""
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
        local _, questsMissing, _, gossipMissing, _, sessionMissing = ns.Capture:Summary()
        local missing = questsMissing + gossipMissing
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
