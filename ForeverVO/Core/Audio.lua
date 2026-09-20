local _, ns = ...

local Audio = {
    dialogMuted = false,
}
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
    if ns.db.muteGameDialog and channel ~= "Dialog" and not Audio.dialogMuted then
        -- Silence the game's own NPC voice lines while ours are playing
        Audio.dialogMuted = C_CVar.GetCVarBool("Sound_EnableDialog")
        if Audio.dialogMuted then
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

--- Called when the queue drains: restore the dialog channel if we muted it.
function Audio.Idle()
    if Audio.dialogMuted then
        SetCVar("Sound_EnableDialog", "1")
        Audio.dialogMuted = false
    end
end
