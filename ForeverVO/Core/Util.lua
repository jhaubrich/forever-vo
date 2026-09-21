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

--- Escapes a literal string for use as a Lua pattern, optionally matching it in
--- either case, so a name or class can be found however the server wrote it.
local function LiteralPattern(text, ignoreCase)
    return (text:gsub(".", function(char)
        if ignoreCase and char:match("%a") then
            return "[" .. char:lower() .. char:upper() .. "]"
        elseif char:match("%W") then
            return "%" .. char
        end
        return char
    end))
end

--- Puts the server's own placeholders back where the client expanded them.
--- $n, $c and $r are resolved against the character reading the line before any
--- addon can see the text, so a line first seen on a rogue is stored saying
--- "rogue" and would be voiced that way for everyone. Capitalisation is kept, so
--- a capitalised match becomes $N/$C/$R and the pipeline reads it as a
--- sentence-initial "Adventurer". Defaults to the current character.
function Util.Tokenize(text, playerName, className, raceName)
    if not text or text == "" then
        return text
    end
    if playerName == nil then playerName = UnitName("player") end
    if className == nil then className = UnitClass("player") end
    if raceName == nil then raceName = UnitRace("player") end
    local function put(subject, value, token)
        if not value or value == "" then
            return subject
        end
        return (subject:gsub(LiteralPattern(value, true), function(match)
            return match:match("^%u") and token:upper() or token
        end))
    end
    text = put(text, playerName, "$n")
    local firstName = playerName and playerName:match("%S+")   -- $n is the bare first name
    if firstName and firstName ~= playerName then
        text = put(text, firstName, "$n")
    end
    text = put(text, className, "$c")
    text = put(text, raceName, "$r")
    return text
end

--- Strips everything that can vary between characters and clients: case,
--- punctuation, whitespace, and the reader's own name, class and race, whether
--- the text still carries the placeholders or the client already expanded them.
--- Both sides have to agree: pack text keeps $c, live text says "hunter", and
--- only dropping each leaves the same string to hash.
function Util.NormalizeText(text, playerName, className, raceName)
    if not text then
        return ""
    end
    text = Util.Tokenize(text, playerName, className, raceName)
    text = text:lower()
    text = text:gsub("%$g[^;]*;", "")   -- $g male:female; branch
    text = text:gsub("%$%a", "")        -- $n, $c, $r, $b, ...
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

function Util.TextKey(text, playerName, className, raceName)
    return Util.HashText(Util.NormalizeText(text, playerName, className, raceName))
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
