local _, ns = ...

-- Whether we turned the Dialog channel off is ns.db.dialogMuted, not a local:
-- the client saves Sound_EnableDialog to Config.wtf, so a reload or logout
-- mid-line would otherwise leave the game's own barks off for good.
local Audio = {}
ns.Audio = Audio

local CHANNEL_CVARS = {
    Master = nil,
    Dialog = "Sound_EnableDialog",
    SFX = "Sound_EnableSFX",
    Music = "Sound_EnableMusic",
    Ambience = "Sound_EnableAmbience",
}

--- Whether the game's sound settings allow our files to be heard at all.
function Audio.IsEnabled()
    if not C_CVar.GetCVarBool("Sound_EnableAllSound") then
        return false
    end
    -- The SFX channel must be enabled for PlaySoundFile to play on any channel
    if not C_CVar.GetCVarBool("Sound_EnableSFX") then
        return false
    end
    local cvar = CHANNEL_CVARS[ns.db.soundChannel]
    return cvar == nil or C_CVar.GetCVarBool(cvar)
end

--- Plays the file and stops it right away to learn whether it exists on disk.
function Audio.Exists(path)
    local willPlay, handle = PlaySoundFile(path)
    if willPlay and handle then
        StopSound(handle)
    end
    return willPlay and true or false
end

--- Starts playback. Returns the sound handle or nil.
function Audio.Play(path)
    local channel = ns.db.soundChannel or "Master"
    local willPlay, handle = PlaySoundFile(path, channel)
    if not willPlay then
        return nil
    end
    if ns.db.muteGameDialog and channel ~= "Dialog" and not ns.db.dialogMuted then
        -- Silence the game's own NPC voice lines while ours are playing
        if C_CVar.GetCVarBool("Sound_EnableDialog") then
            ns.db.dialogMuted = true
            SetCVar("Sound_EnableDialog", "0")
        end
    end
    return handle
end

function Audio.Stop(handle)
    if handle then
        StopSound(handle)
    end
end

--- Called when the queue drains or pauses: restore the dialog channel if we muted it.
function Audio.Idle()
    if ns.db and ns.db.dialogMuted then
        SetCVar("Sound_EnableDialog", "1")
        ns.db.dialogMuted = false
    end
end

-- A session that ended mid-line (a crash skips PLAYER_LOGOUT) left the channel
-- off; nothing is playing yet, so turn it back on. On logout, restore it before
-- the client writes Config.wtf.
ns.OnInit(function()
    Audio.Idle()
    local frame = CreateFrame("Frame")
    frame:RegisterEvent("PLAYER_LOGOUT")
    frame:SetScript("OnEvent", Audio.Idle)
end)
