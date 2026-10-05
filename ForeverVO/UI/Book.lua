local _, ns = ...
local Packs, Queue = ns.Packs, ns.Queue

--[[
A "Play" button on the book window (ItemTextFrame, Blizzard_UIPanels_Game),
for the page on screen. Opening a book or turning a page reads nothing on its
own (Events.ITEM_TEXT_READY only captures the page and hands it here); once a
page is playing, turning the page or closing the book leaves it reading, so
the player can read along or walk away. Play reads on through every voiced
page after this one that the pack links (x on a page record; the client hands
an addon only the page on screen), and after Play each page of that book the
player turns to queues too, once per reading, which carries on past a page the
chain does not reach. Stop, opening another book, or the last queued page
finishing ends the reading; Play again starts a new one.

The frame hides its button bar and the parchment runs to its bottom edge, and
on a book of several pages the arrows and page number fill the top row, so the
button hangs below the bottom-right corner, the way a tab does. It is parented
to ItemTextFrame and shows and hides with it.
]]

local Book = {
    page = nil,     -- queue item for the page on screen (path nil when unvoiced)
    queued = {},    -- path -> queue item started from this button
    reading = nil,  -- title of the book Play was pressed in: its pages queue as shown
    heard = {},     -- path -> true for pages queued in this reading
}
ns.UI.Book = Book

function Book:GetButton()
    if self.button then
        return self.button
    end
    if not ItemTextFrame then
        return nil
    end
    local button = CreateFrame("Button", nil, ItemTextFrame, "UIPanelButtonTemplate")
    button:SetSize(70, 22)
    button:SetPoint("TOPRIGHT", ItemTextFrame, "BOTTOMRIGHT", -6, -2)
    button:SetText("Play")
    button:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        Book:OnClick()
    end)
    button:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:SetText(self:IsEnabled() and "Read this page aloud" or "No voiceover for this page", 1, 1, 1)
        GameTooltip:Show()
    end)
    button:SetScript("OnLeave", GameTooltip_Hide)
    self.button = button
    return button
end

--- The page on screen changed (opened, or turned to): item is what Play queues.
function Book:ShowPage(item)
    self.page = item
    if self.reading and self.reading ~= item.name then
        self.reading, self.heard = nil, {}
    end
    if self.reading and item.path and not self.heard[item.path] then
        self:Enqueue(item)
    end
    self:Update()
end

function Book:Update()
    local button = self:GetButton()
    if not button then
        return
    end
    local item = self.page
    button:SetEnabled(item ~= nil and item.path ~= nil)
    local queued = item and item.path and self.queued[item.path]
    button:SetText(queued and Queue:Contains(queued) and "Stop" or "Play")
end

function Book:OnClick()
    local item = self.page
    if not item or not item.path then
        return
    end
    local path = item.path
    local queued = self.queued[path]
    if queued and Queue:Contains(queued) then
        -- Stop ends the reading: the pages queued after this one go too
        self.reading, self.heard = nil, {}
        local all = {}
        for _, each in pairs(self.queued) do
            all[#all + 1] = each
        end
        for _, each in ipairs(all) do
            Queue:Remove(each)
        end
        return
    end
    -- A new reading: every linked page is read again, except one still queued
    self.reading, self.heard = item.name, {}
    local items = { self:Prepare(item) }
    -- On through the pages the pack links after this one, queued together so
    -- every file is checked before the first starts (Queue:AddMany)
    local entry, seen = item.entry, 0
    while seen < 100 do
        seen = seen + 1
        local hash, page, pack = Packs:NextBookPage(entry)
        if not hash then
            break
        end
        local nextPath, duration, voice = Packs:BookSound(pack, hash, page)
        local waiting = self.queued[nextPath]
        if not self.heard[nextPath] and not (waiting and Queue:Contains(waiting)) then
            items[#items + 1] = self:Prepare({
                kind = "book", event = "page", text = page.t, name = item.name,
                title = page.p and format("Page %d", page.p) or nil,
                isObject = true, path = nextPath, duration = duration, pack = pack, voice = voice, entry = page,
            })
        end
        entry = page
    end
    Queue:AddMany(items)
    -- A page the queue turned away (its file missing) will never stop
    for queuedPath, each in pairs(self.queued) do
        if not Queue:Contains(each) then
            self.queued[queuedPath] = nil
        end
    end
    self:Update()
end

function Book:Enqueue(item)
    Queue:Add(self:Prepare(item))
    self:Update()
end

--- The queue item for a page, remembered as heard in this reading and as
--- started from this button.
function Book:Prepare(item)
    local path = item.path
    self.heard[path] = true
    -- A fresh copy, so pressing Play again after it finished queues it anew
    local copy = {}
    for key, value in pairs(item) do
        copy[key] = value
    end
    copy.onStop = function()
        if self.queued[path] == copy then
            self.queued[path] = nil
        end
        -- The last page of the reading done (or stopped): the reading is over,
        -- so turning pages afterwards queues nothing until Play again
        if next(self.queued) == nil then
            self.reading, self.heard = nil, {}
        end
        self:Update()
    end
    self.queued[path] = copy
    return copy
end

ns.OnInit(function()
    Queue:RegisterCallback("OnPacksChanged", function()
        if ItemTextFrame and ItemTextFrame:IsShown() then
            Book:Update()
        end
    end, Book)
end)
