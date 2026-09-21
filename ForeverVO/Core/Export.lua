local _, ns = ...
local Util = ns.Util

--[[
"/fvo export": packs this session's unvoiced lines into a string players can
paste into a GitHub issue (see .github/ISSUE_TEMPLATE/capture.yml). The
string is JSON, zlib-compressed and base64-encoded with the client's own
C_EncodingUtil, prefixed with "FVO1:". The character's name, class and race are
replaced by the $n/$c/$r placeholders (Util.Tokenize) at capture, so nothing
identifying leaves the client and no line is voiced for one class only.
tools/exportfile.py decodes it.
]]

local Export = {
    nudged = false,
}
ns.Export = Export

local NUDGE_AFTER = 10
local PREFIX = "FVO1:"

--- Builds the export table from ForeverVOCaptureDB: only lines without audio.
function Export:Collect()
    local db = ForeverVOCaptureDB or {}
    local lines, npcs, used = {}, {}, {}
    local function add(kind, entry)
        if entry.found then
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
    for key in pairs(used) do
        local npc = (db.npcs or {})[key]
        if npc then
            npcs[key] = {
                name = npc.name, sex = npc.sex, displayID = npc.displayID,
                modelFileID = npc.modelFileID, creatureType = npc.creatureType, isObject = npc.isObject,
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

function Export:Encode()
    local data = self:Collect()
    if #data.lines == 0 then
        return nil, 0
    end
    local json = C_EncodingUtil.SerializeJSON(data)
    local compressed = C_EncodingUtil.CompressString(json, Enum.CompressionMethod.Zlib)
    return PREFIX .. C_EncodingUtil.EncodeBase64(compressed), #data.lines
end

-- ---------------------------------------------------------------------------
-- Dialog
-- ---------------------------------------------------------------------------

function Export:GetFrame()
    if self.frame then
        return self.frame
    end
    local frame = CreateFrame("Frame", "ForeverVOExportFrame", UIParent, "ButtonFrameTemplate")
    self.frame = frame
    frame:SetSize(520, 340)
    frame:SetPoint("CENTER")
    frame:SetFrameStrata("DIALOG")
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
    frame.Hint:SetText("Press Ctrl+C to copy, then paste it as a comment on\n|cff6ec6ffgithub.com/quinn-dougherty/forever-vo/issues/1|r\nYour character name has been removed.")

    local scroll = CreateFrame("ScrollFrame", nil, frame, "InputScrollFrameTemplate")
    scroll:SetPoint("TOPLEFT", frame.Hint, "BOTTOMLEFT", 0, -12)
    scroll:SetPoint("BOTTOMRIGHT", -30, 16)
    scroll.EditBox:SetMaxLetters(0)
    scroll.EditBox:SetFontObject("GameFontHighlightSmall")
    scroll.EditBox:SetScript("OnEscapePressed", function() frame:Hide() end)
    scroll.EditBox:SetScript("OnTextChanged", function(editBox, userInput)
        if userInput then
            editBox:SetText(frame.exportString or "")
            editBox:HighlightText()
        end
    end)
    if scroll.CharCount then
        scroll.CharCount:Hide()
    end
    frame.Scroll = scroll
    return frame
end

function Export:Show()
    local text, count = self:Encode()
    if not text then
        ns.Print("nothing to export yet: every line seen this session already has audio.")
        return
    end
    local frame = self:GetFrame()
    frame.exportString = text
    frame.Scroll.EditBox:SetText(text)
    frame:Show()
    frame.Scroll.EditBox:SetFocus()
    frame.Scroll.EditBox:HighlightText()
    ns.Print(format("%d %s without audio packed into %d characters.", count, Util.Plural(count, "line"), #text))
end

--- Called by Capture after each recorded line; reminds the player once per session.
function Export:OnLineCaptured(found)
    if found or self.nudged then
        return
    end
    self.count = (self.count or 0) + 1
    if self.count >= NUDGE_AFTER then
        self.nudged = true
        ns.Print(format("%d lines seen this session have no voice yet. |cffffd100/fvo export|r to contribute them.", self.count))
    end
end

-- Last-chance reminder when a logout or quit timer starts (the beta client
-- forgets the capture between sessions, so lines not exported now are lost
-- to the export path).
ns.OnInit(function()
    local frame = CreateFrame("Frame")
    frame:RegisterEvent("PLAYER_CAMPING")
    frame:RegisterEvent("PLAYER_QUITING")
    frame:SetScript("OnEvent", function()
        local _, questsMissing, _, gossipMissing = ns.Capture:Summary()
        local missing = questsMissing + gossipMissing
        if missing > 0 then
            ns.Print(format("%d unvoiced %s seen this session. |cffffd100/fvo export|r before you go, or they are forgotten.",
                missing, Util.Plural(missing, "line")))
        end
    end)
end)
