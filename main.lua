local Engine = require("trentnav/engine")
local Navigation = require("trentnav/navigation")
local config = require("config")
local vector = require("common/geometry/vector_3")
local color = require("common/color")

local navigation = Navigation.new(core, config.navigation, vector)
local engine = Engine.new(core, config, function() return navigation end, vector)

local tree = core.menu.tree_node()
local enabled = core.menu.checkbox(false, "trentnav_enabled")
local retry = core.menu.button("trentnav_retry")
local status = core.menu.header()
local nav_status = core.menu.header()
local diagnostic = core.menu.button("trentnav_diagnostic")
local unloaded = false
enabled:set(false)

core.register_on_update_callback(function()
    if unloaded then return end
    local success, reason = pcall(function()
        if core.input.is_key_pressed(config.emergency_stop_key) then
            enabled:set(false)
            navigation:stop()
        end
        engine:set_enabled(enabled:get_state())
        engine:tick()
        navigation:tick(engine.enabled and not config.dry_run and engine.state == "moving", function(player, path, index)
            local world = engine.safety:snapshot(player)
            if world.engaged > 0 then return false, "Combat detected before player flag" end
            if player:get_health() / math.max(1, player:get_max_health()) < config.minimum_health then
                return false, "Health dropped during movement"
            end
            return engine.safety:path_clear(world, path, index, engine.reserved_guid, config.safety.route_recheck_distance)
        end)
    end)
    if not success then
        enabled:set(false)
        pcall(function() engine:set_enabled(false) end)
        pcall(function() navigation:stop() end)
        core.log_error("[TrentNav] Stopped after API error: " .. tostring(reason))
    end
end)

core.register_on_game_event_callback(function(event)
    if not unloaded then
        if event == "PLAYER_ENTERING_WORLD" or event == "PLAYER_REGEN_DISABLED" then engine:stop_navigation() end
        engine:on_event(event)
    end
end)

core.register_on_render_menu_callback(function()
    if unloaded then return end
    tree:render("TrentNav - Polestar Questing", function()
        enabled:render(config.dry_run and "Enable read-only diagnostics" or "Enable questing",
            "F9 stops TrentNav. Uses the LIVE Polestar step; no external navigation server.")
        status:render(engine.state .. ": " .. engine.message, color.white(255))
        nav_status:render(navigation:get_status(), color.white(255))
        if retry:render("Retry current goal") then engine:reset() end
        if diagnostic:render("Log test snapshot (read-only)") then
            local success, reason = pcall(function()
                local player = core.object_manager.get_local_player()
                if player and player:is_valid() then core.log("[TrentNav] " .. engine:diagnose(player)) end
                core.log("[TrentNav] " .. navigation:get_status())
            end)
            if not success then core.log_error("[TrentNav] Diagnostic failed: " .. tostring(reason)) end
        end
    end)
end)

return {
    name = "TrentNav",
    version = "0.2.0",
    unload = function()
        unloaded = true
        navigation:close()
        engine:set_enabled(false)
    end,
}
