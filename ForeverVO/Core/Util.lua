local _, ns = ...

local Util = {}
ns.Util = Util

-- ---------------------------------------------------------------------------
-- GUIDs
-- ---------------------------------------------------------------------------

--- Returns the unit type ("Creature", "Vehicle", "GameObject", "Player", ...) and the
--- WorldObject ID for GUID types that carry one.
function Util.ParseGUID(guid)
    if not guid then
        return nil, nil
    end
    local unitType, _, _, _, _, id = strsplit("-", guid)
    if unitType == "Creature" or unitType == "Vehicle" or unitType == "GameObject" then
        return unitType, tonumber(id)
    end
    return unitType, nil
end

--- Speaker key used by packs: positive creature ID, negative game object ID.
function Util.SpeakerKeyFromGUID(guid)
    local unitType, id = Util.ParseGUID(guid)
    if not id then
        return nil
    end
    if unitType == "GameObject" then
        return -id
    end
    return id
end

function Util.IsCreatureKey(key)
    return key ~= nil and key > 0
end

--- The unit token for the NPC the player is talking to, if any.
function Util.DialogUnit()
    if UnitExists("questnpc") then
        return "questnpc"
    elseif UnitExists("npc") then
        return "npc"
    end
end

-- ---------------------------------------------------------------------------
-- Text normalisation and hashing (mirrored by tools/textkey.py)
-- ---------------------------------------------------------------------------

--- Strips everything that can vary between clients: case, punctuation, whitespace,
--- and the player's own name.
function Util.NormalizeText(text, playerName)
    if not text then
        return ""
    end
    text = text:lower()
    if playerName and playerName ~= "" then
        text = text:gsub(playerName:lower(), "", 1) -- plain replace of the first occurrence
    end
    text = text:gsub("[^a-z0-9]", "")
    return text
end

--- djb2 hash modulo 2^32 as 8 hex characters. Stays within double precision.
function Util.HashText(normalized)
    local hash = 5381
    for i = 1, #normalized do
        hash = (hash * 33 + normalized:byte(i)) % 4294967296
    end
    return format("%08x", hash)
end

function Util.TextKey(text, playerName)
    return Util.HashText(Util.NormalizeText(text, playerName))
end

--- Word set for fuzzy matching (lowercase alphanumeric words).
local function WordSet(text)
    local set, count = {}, 0
    for word in text:lower():gmatch("[a-z0-9']+") do
        if not set[word] then
            set[word] = true
            count = count + 1
        end
    end
    return set, count
end

--- Jaccard similarity of the two texts' word sets, 0..1.
function Util.Similarity(a, b)
    local setA, countA = WordSet(a)
    local setB, countB = WordSet(b)
    if countA == 0 or countB == 0 then
        return 0
    end
    local shared = 0
    for word in pairs(setA) do
        if setB[word] then
            shared = shared + 1
        end
    end
    return shared / (countA + countB - shared)
end

-- ---------------------------------------------------------------------------
-- Misc
-- ---------------------------------------------------------------------------

function Util.PlayerGenderPrefix()
    local sex = UnitSex("player")
    if sex == 2 then
        return "m-"
    elseif sex == 3 then
        return "f-"
    end
    return ""
end

--- Splits spoken text into sentence-aligned pages no longer than maxChars.
function Util.Paginate(text, maxChars)
    local pages, current = {}, ""
    for sentence in (text or ""):gmatch("[^%.%!%?]+[%.%!%?]*%s*") do
        if current ~= "" and #current + #sentence > maxChars then
            table.insert(pages, strtrim(current))
            current = sentence
        else
            current = current .. sentence
        end
    end
    if strtrim(current) ~= "" then
        table.insert(pages, strtrim(current))
    end
    if #pages == 0 then
        pages[1] = text or ""
    end
    return pages
end

function Util.Plural(count, singular, plural)
    return count == 1 and singular or (plural or singular .. "s")
end
