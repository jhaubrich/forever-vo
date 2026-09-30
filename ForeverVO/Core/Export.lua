local _, ns = ...
local Util = ns.Util

--[[
"/fvo export": packs the capture's unvoiced lines heard since the last export
(across sessions and characters now that the beta reads saved variables back)
into a string players can
paste into a GitHub issue (see .github/ISSUE_TEMPLATE/capture.yml). The
window shows that issue as text, and Copy Link builds the form URL with the
string in it, the same way /fvo report does. The export stamps
ForeverVOCaptureDB.exportedAt when the window is shown, and Capture.Exported
skips what an earlier export packed: before that, every export carried the
whole DB, and a player who exported after each quest, as the per-line
reminder suggested, sent the same hundred lines a hundred times. "/fvo export
all" packs everything again, for a string that was shown but never pasted;
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
-- Past this, GitHub returns 414 URI Too Long. The export string is the only
-- field that grows without a bound, so a long one is left out of the link.
local URL_BUDGET = 6000
local NOTE = "## Note:\n\n"
local PRESS_COPY = "Click the link, then Ctrl+C"
local PRESS_COPY_SHORT = "Click the link, then Ctrl+C. Paste the export string into the form; it was too long for the link."

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

--- Builds the export table from ForeverVOCaptureDB: lines without audio, and
--- voiced lines the pack asked to hear again from a reader like this one,
--- heard since the last export unless `all`.
function Export:Collect(all)
    local db = ForeverVOCaptureDB or {}
    local lines, npcs, used = {}, {}, {}
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
        if entry.npc then
            used[entry.npc] = true
        end
    end
    for _, entry in pairs(db.quests or {}) do
        add("quest", entry)
    end
    for _, entry in pairs(db.gossip or {}) do
        add("gossip", entry)
    end
    local at = db.exportedAt
    for key, npc in pairs(db.npcs or {}) do
        if npc.recast and (all or at == nil or npc.recast >= at) then
            used[key] = true
        end
    end
    for key in pairs(used) do
        local npc = (db.npcs or {})[key]
        if npc then
            npcs[key] = {
                name = npc.name, sex = npc.sex, displayID = npc.displayID,
                modelFileID = npc.modelFileID, creatureType = npc.creatureType, isObject = npc.isObject,
                addon = npc.addon, sexes = npc.sexes,
            }
        end
    end
    return {
        v = 1,
        addon = ns.version,
        build = select(2, GetBuildInfo()),
        lines = lines,
        npcs = npcs,
    }
end

function Export:Encode(all)
    local data = self:Collect(all)
    if #data.lines == 0 then
        return nil, 0
    end
    local json = C_EncodingUtil.SerializeJSON(data)
    local compressed = C_EncodingUtil.CompressString(json, Enum.CompressionMethod.Zlib)
    return PREFIX .. C_EncodingUtil.EncodeBase64(compressed), #data.lines
end

--- The issue text. The note is where the cursor starts. The facts under it
--- are labels, and the export string stays whole at the bottom so a note
--- does not land in the middle of it.
function Export:Body(data, payload)
    local count = #data.lines
    local summary = format("%d %s. Your character name has been removed.",
        count, Util.Plural(count, "line"))
    local facts = { "Lines: " .. count, "Addon: " .. (data.addon or "dev") }
    if data.build and data.build ~= "" then
        table.insert(facts, "Build: " .. tostring(data.build))
    end
    return format("## Captured lines\n\n%s\n\n%s\n\n%s\n\n## Export string\n\n%s",
        summary, NOTE, table.concat(facts, "\n"), payload)
end

--- The capture form's export field is the whole text, and the decoder finds
--- the FVO1 string inside it. A string that will not fit in the URL is left
--- out; the form still opens on the right template.
function Export:Link(body, count)
    local title = format("Captured lines (%d)", count)
    local function urlFor(text)
        return Query({
            { "template", "capture.yml" },
            { "title", title },
            { "export", text },
        })
    end
    local url = urlFor(body)
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
    frame.Hint:SetText("Add a note if you like, then click Copy Link and paste it into your browser. It opens a GitHub form with all of this filled in.\n\nOne export covers everything you have seen since the last one, so there is no need to do this after every quest.")

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

    frame.CopyButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.CopyButton:SetSize(110, 22)
    frame.CopyButton:SetPoint("BOTTOMRIGHT", -16, 12)
    frame.CopyButton:SetText("Copy Link")
    frame.CopyButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Export:CopyLink()
    end)

    frame.Status = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
    frame.Status:SetPoint("RIGHT", frame.CopyButton, "LEFT", -12, 0)
    frame.Status:SetJustifyH("RIGHT")
    frame.Status:SetText("")
    return frame
end

function Export:ClearStatus()
    local frame = self.frame
    if not frame then
        return
    end
    frame.Status:SetText("")
    if frame.LinkBox:IsShown() then
        frame.LinkBox:Hide()
        PlaceScroll(frame, 46)
    end
end

function Export:CopyLink()
    local frame = self.frame
    local body = frame.Scroll.EditBox:GetText() or ""
    local url, shortened = self:Link(body, frame.count or 0)
    frame.link = url
    -- No SetFocus here. A button click that takes keyboard focus is the
    -- protected call the client reports as UNKNOWN(). Clicking the link is
    -- what focuses it, and OnEditFocusGained selects the text.
    PlaceScroll(frame, 70)
    frame.LinkBox:Show()
    frame.LinkBox:SetText(url)
    frame.Status:SetText(shortened and PRESS_COPY_SHORT or PRESS_COPY)
    frame.Status:SetTextColor(1, 0.82, 0)
end

---@param all boolean pack every line, exported before or not
function Export:Show(all)
    local db = ForeverVOCaptureDB or {}
    local data = self:Collect(all)
    if #data.lines == 0 then
        if not all and db.exportedAt then
            ns.Print("nothing new since your last export. |cffffd100/fvo export all|r packs everything again.")
        else
            ns.Print("nothing to export: every line seen so far already has audio.")
        end
        return
    end
    local payload = self:Encode(all)
    local frame = self:GetFrame()
    frame.count = #data.lines
    self:ClearStatus()
    local editBox = frame.Scroll.EditBox
    local body = self:Body(data, payload)
    editBox:SetText(body)
    frame:Show()
    editBox:SetFocus()
    local at = body:find(NOTE, 1, true)
    editBox:SetCursorPosition(at and (at + #NOTE - 1) or 0)
    local since = (not all and db.exportedAt) and " heard since your last export" or ""
    db.exportedAt = time()
    ns.Print(format("%d %s%s packed into %d characters. You do not have to do this for each quest.",
        frame.count, Util.Plural(frame.count, "line"), since, #payload))
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
