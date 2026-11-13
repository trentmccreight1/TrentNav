local Safety = require("trentnav/safety")
local Engine = {}
Engine.__index = Engine

local function valid(object)
    return object and object:is_valid()
end

local function distance(first, second)
    local delta_x = first.x - second.x
    local delta_y = first.y - second.y
    local delta_z = first.z - second.z
    return math.sqrt(delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)
end

local function open(goal)
    return not goal.is_complete and (goal.status == "active" or goal.status == "pending")
end

function Engine.new(api, config, navigation, vector)
    return setmetatable({
        api = api,
        config = config,
        navigation = navigation,
        vector = vector,
        safety = Safety.new(api, config),
        cooldowns = {},
        enabled = false,
        state = "disabled",
        message = "Enable TrentNav to start",
        last_tick = -math.huge,
        next_action = 0,
        generation = 0,
        attempts = 0,
    }, Engine)
end

function Engine:status(state, message)
    if self.state ~= state or self.message ~= message then
        self.state, self.message = state, message
        self.api.log("[TrentNav] " .. state .. ": " .. message)
    end
end

function Engine:stop_navigation()
    self.generation = self.generation + 1
    local owned = self.owned_navigation
    self.owned_navigation, self.destination, self.navigation_phase = nil, nil, nil
    if owned then owned:stop() end
end

function Engine:set_enabled(enabled)
    if self.enabled == enabled then return end
    self.enabled = enabled
    self:stop_navigation()
    self:release_target()
    self.goal_key, self.panel_stage = nil, nil
    self:status(enabled and "waiting" or "disabled", enabled and "Waiting for live guide" or "Stopped")
end

function Engine:pause(message)
    self:stop_navigation()
    self:release_target()
    self:status("paused", message)
end

function Engine:release_target()
    local player = self.api.object_manager.get_local_player()
    if self.selected_guid and valid(player) and not player:is_in_combat()
        and not (self.world and self.world.engaged > 0) then
        local target = player:get_target()
        if valid(target) and target:get_guid() == self.selected_guid then self.api.input.clear_target() end
    end
    self.selected_guid, self.reserved_guid, self.safe_since, self.handoff_at = nil, nil, nil, nil
end

function Engine:reset()
    self:stop_navigation()
    self:release_target()
    self.goal_key, self.panel_stage = nil, nil
    self:status("waiting", "Retrying live goal")
end

function Engine:on_event(event)
    if not self.enabled then return end
    local stages = { QUEST_DETAIL = "detail", QUEST_PROGRESS = "progress", QUEST_COMPLETE = "reward" }
    if stages[event] then self.panel_stage = stages[event] end
    if event == "QUEST_FINISHED" then self.panel_stage = nil end
    if event == "PLAYER_ENTERING_WORLD" then self:reset() end
end

function Engine:choose_goal(step)
    local fallback
    for _, goal in ipairs(step.goals or {}) do
        if open(goal) and not goal.optional and goal.type ~= "note" then
            if goal.is_current or goal.index == step.current_goal then return goal end
            fallback = fallback or goal
        end
    end
    return fallback
end

function Engine:rule(goal)
    local rules = self.config.objective_rules[goal.quest_id or 0]
    return rules and rules[goal.objective or 0]
end

function Engine:hold(message)
    self:stop_navigation()
    self:status("holding", message)
end

function Engine:reject_target(reason, now)
    if self.reserved_guid then self.cooldowns[self.reserved_guid] = now + self.config.safety.target_cooldown end
    self:stop_navigation()
    self:release_target()
    self.attempts = 0
    self:status("holding", reason)
end

function Engine:movement_failed(reason, now)
    self.attempts = self.attempts + 1
    self.navigation_error = reason
    if self.reserved_guid then self:reject_target(reason, now)
    else
        self:stop_navigation()
        self.next_navigation = now + 2
        self:status("waiting", reason)
    end
end

function Engine:move(player, position, radius, now)
    local endpoint_clear, endpoint_reason = self.safety:segment_clear(self.world, position, position, self.reserved_guid)
    if not endpoint_clear then
        if self.reserved_guid then self:reject_target(endpoint_reason, now) else self:hold(endpoint_reason) end
        return false
    end
    if distance(player:get_position(), position) <= radius then
        self:stop_navigation()
        self.attempts = 0
        return true
    end
    local client = self.navigation()
    local required = { "plan_route", "follow_path", "get_current_path", "get_path_index", "stop", "is_server_available" }
    for _, method in ipairs(required) do
        if not client or type(client[method]) ~= "function" then
            self:pause("Owned navigation client missing safety API: " .. method)
            return false
        end
    end
    if not client:is_server_available() then
        self:pause(client:get_status())
        return false
    end
    if self.owned_navigation and self.owned_navigation ~= client then self:stop_navigation() end
    if self.destination and distance(self.destination, position) > 3 then self:stop_navigation() end
    if self.destination then
        local timeout = self.navigation_phase == "planning" and self.config.safety.planning_timeout or self.config.navigation_timeout
        if now - self.navigation_started > timeout then
            self:movement_failed("Navigation " .. self.navigation_phase .. " timed out", now)
            return false
        end
        if self.navigation_phase == "moving" then
            local clear, reason = self.safety:path_clear(self.world, client:get_current_path(),
                client:get_path_index(), self.reserved_guid, self.config.safety.route_recheck_distance)
            if not clear then
                if self.reserved_guid then self:reject_target(reason, now) else self:hold(reason) end
                self.next_navigation = now + 2
                return false
            end
        end
        self:status(self.navigation_phase, self.goal.text or self.goal.type)
        return false
    end
    if now < (self.next_navigation or 0) then return false end
    if self.attempts >= self.config.max_navigation_attempts then
        self:pause(self.navigation_error or "Navigation retry limit reached; use Retry")
        return false
    end
    self.generation = self.generation + 1
    local generation = self.generation
    local origin = self.vector.new(self.world.origin.x, self.world.origin.y, self.world.origin.z)
    local destination = self.vector.new(position.x, position.y, position.z)
    local map_id, ignore_guid = self.api.get_instance_id(), self.reserved_guid
    self.destination, self.navigation_phase = destination, "planning"
    self.owned_navigation = client
    self.navigation_started = now
    self:status("planning", "Checking a mesh route before walking")
    client:plan_route({ origin, destination }, function(success, data)
        if self.generation ~= generation then return end
        local ok, error_message = pcall(function()
            local current = self.api.object_manager.get_local_player()
            if not self.enabled or not valid(current) or self.navigation() ~= client
                or self.api.get_instance_id() ~= map_id then
                self:stop_navigation()
                return
            end
            if current:is_in_combat() or current:is_dead() or current:is_ghost()
                or current:is_casting_spell() or current:is_channelling_spell() or current:is_on_taxi()
                or current:get_health() / math.max(1, current:get_max_health()) < self.config.minimum_health then
                self:hold("Player state changed during route planning")
                return
            end
            local world = self.safety:snapshot(current)
            if world.engaged > 0 or distance(world.origin, origin) > 2 then
                self:hold("Player moved or combat started during route planning")
                return
            end
            if ignore_guid then
                local safe, reason = self.safety:assess(current, world, world.by_guid[ignore_guid], self.target_ids)
                if not safe then self:reject_target(reason, self.api.time()); return end
            end
            local path = success and data and data.waypoints
            if not path or #path == 0 then
                self:movement_failed(data and data.reason or "Mesh planning failed or returned no route", self.api.time())
                return
            end
            local clear, reason = self.safety:path_clear(world, path, 1, ignore_guid)
            if not clear then
                if ignore_guid then self:reject_target(reason, self.api.time()) else self:hold(reason) end
                self.next_navigation = self.api.time() + 2
                return
            end
            if distance(path[#path], destination) > 3 then
                self:movement_failed("Refusing a partial route that misses the destination", self.api.time())
                return
            end
            self.owned_navigation, self.navigation_phase = client, "moving"
            self.navigation_started = self.api.time()
            client:follow_path(path, function(arrived, reason)
                if self.generation ~= generation then return end
                self.owned_navigation, self.destination, self.navigation_phase = nil, nil, nil
                self.next_navigation = self.api.time() + 1
                if arrived then self.attempts = 0
                else self:movement_failed("Navigation failed: " .. tostring(reason), self.api.time()) end
            end)
        end)
        if not ok then self:pause("Route safety check failed: " .. tostring(error_message)) end
    end, { start_pos = origin, map_id = map_id, optimize = false, return_to_start = false,
        allow_partial = false, avoid_zones = self.safety:avoid_zones(self.world, ignore_guid) })
    return false
end

function Engine:goal_position(player, goal)
    local rule = self:rule(goal)
    local position = rule and rule.position or (goal.has_position and goal.position)
    if not position then return nil end
    local continent_id = goal.continent_id
    if rule and rule.position then continent_id = rule.continent_id end
    if continent_id ~= self.api.get_instance_id() then
        self:pause("Goal is on another instance map; travel manually, then Retry")
        return nil
    end
    if position.z == 0 then
        local probe = self.vector.new(position.x, position.y, player:get_position().z + 5)
        local height = self.api.get_height_for_position(probe)
        if height == 0 then
            self:pause("Unknown goal height; move closer or supply a rule position")
            return nil
        end
        return self.vector.new(position.x, position.y, height)
    end
    return self.vector.new(position.x, position.y, position.z)
end

function Engine:find(player, ids, predicate, radius)
    local best, best_distance
    local origin = player:get_position()
    for _, object in ipairs(self.api.object_manager.get_all_objects()) do
        if valid(object) and ids[object:get_npc_id()] and predicate(object) then
            local current_distance = distance(origin, object:get_position())
            if current_distance <= radius and (not best_distance or current_distance < best_distance) then
                best, best_distance = object, current_distance
            end
        end
    end
    return best
end

function Engine:act(now, callback)
    if now < self.next_action then return false end
    self.next_action = now + self.config.action_interval
    callback()
    return true
end

function Engine:quest_dialog(goal, now)
    local quests = self.api.quests
    local quest_id = goal.quest_id or 0
    if quest_id <= 0 then self:pause("Quest goal has no real quest ID"); return true end
    local panel = quests.get_open_quest_info()
    if panel then
        self:stop_navigation()
        if panel.quest_id ~= quest_id then
            self:pause("Another quest panel is open; close it, then Retry")
            return true
        end
        if goal.type == "accept" and self.panel_stage == "detail" then
            self:act(now, function() quests.accept_quest() end)
        elseif goal.type == "turnin" and self.panel_stage == "progress" then
            if quests.is_quest_complete(quest_id) then
                self:act(now, function() quests.complete_quest() end)
            else self:pause("Quest is not ready to turn in") end
        elseif goal.type == "turnin" and self.panel_stage == "reward" then
            local count = quests.get_num_quest_choices()
            local choice = self.config.reward_choices[quest_id]
            if count == 0 then choice = 0 elseif count == 1 then choice = 1 end
            if type(choice) ~= "number" or choice % 1 ~= 0 or choice < 0 or choice > count
                or (count > 0 and choice == 0) then
                self:pause("Choose a quest reward manually or configure reward_choices[" .. quest_id .. "]")
            else self:act(now, function() quests.get_quest_reward(choice) end) end
        else self:pause("Reopen the matching quest panel so its stage can be observed") end
        return true
    end
    if quests.is_gossip_frame_shown() then
        self:stop_navigation()
        local available = goal.type == "accept"
        local entries = available and quests.get_gossip_available_quests() or quests.get_gossip_active_quests()
        for _, entry in ipairs(entries) do
            if entry.quest_id == quest_id then
                self:act(now, function()
                    if available then quests.select_gossip_available_quest(entry.quest_id)
                    else quests.select_gossip_active_quest(entry.quest_id) end
                end)
                return true
            end
        end
        self:pause("Matching quest ID not in gossip; check prerequisites or handle manually")
        return true
    end
    self.panel_stage = nil
    return false
end

function Engine:pursue_target(player, ids, now)
    self.target_ids = ids
    local maximum_mana = player:get_max_power(0)
    if maximum_mana > 0 and player:get_power(0) / maximum_mana < self.config.minimum_mana then
        self:release_target()
        self:hold("Waiting for mana before another pull")
        return true
    end
    local target = self.reserved_guid and self.world.by_guid[self.reserved_guid]
    if self.reserved_guid then
        local safe, reason = self.safety:assess(player, self.world, target, ids)
        if not safe then self:reject_target(reason, now); return true end
    else
        local reason
        target, reason = self.safety:choose(player, self.world, ids, self.cooldowns, now)
        if not target then
            for _, unit in ipairs(self.world.units) do
                if ids[unit.id] then self:hold(reason); return true end
            end
            return false
        end
        self.reserved_guid, self.safe_since = target.guid, now
    end
    if self.selected_guid then
        local selected = player:get_target()
        if not valid(selected) or selected:get_guid() ~= self.selected_guid then
            self:reject_target("Selected target changed outside TrentNav; not overriding it", now)
            return true
        end
        if now - self.handoff_at > self.config.safety.handoff_timeout then
            self:reject_target("Rotation did not engage the selected target; check its target-only settings", now)
        else self:status("handoff", "Waiting for rotation plugin to engage the reserved quest target") end
        return true
    end
    if now - self.safe_since < self.config.safety.safe_observation_seconds then
        self:hold("Observing isolated quest target and nearby patrols")
        return true
    end
    if distance(player:get_position(), target.position) <= self.config.engage_range then
        self:stop_navigation()
        local safe, reason = self.safety:assess(player, self.world, target, ids)
        if not safe then self:reject_target(reason, now); return true end
        self.api.input.set_target(target.object)
        self.selected_guid, self.handoff_at = target.guid, now
        self:status("handoff", "Quest target selected; rotation owns attacks and spells")
        return true
    end
    self:move(player, self.safety:approach(self.world, target), 0.75, now)
    return true
end

function Engine:execute(player, goal, now)
    local rule = self:rule(goal)
    local kind = rule and rule.action or goal.type
    if goal.type == "complete" and not rule and (goal.npc_id or 0) > 0 then
        local objective = self.api.quests.get_quest_objectives(goal.quest_id)[goal.objective]
        if objective then
            local actions = { monster = "kill", item = "collect", object = "interact" }
            kind = actions[objective.type] or kind
        end
    end
    if goal.type == "accept" or goal.type == "turnin" then
        if self:quest_dialog(goal, now) then return end
    end
    if kind == "goto" then
        local position = self:goal_position(player, goal)
        if position and self:move(player, position, 3, now) then
            self:status("waiting", "At guide point; waiting for Polestar to advance")
        elseif not position and self.state ~= "paused" then self:pause("Goal has no world position") end
        return
    end
    local supported = { accept = true, turnin = true, talk = true, interact = true,
        kill = true, collect = true, use = true }
    if not supported[kind] then self:pause("Needs manual action or objective_rules: " .. tostring(kind)); return end
    local ids = {}
    for _, npc_id in ipairs(rule and rule.npc_ids or {}) do ids[npc_id] = true end
    if not next(ids) and (goal.npc_id or 0) > 0 then ids[goal.npc_id] = true end
    if not next(ids) and kind ~= "use" then self:pause("No target IDs; configure objective_rules for this goal"); return end
    local farming = kind == "kill" or kind == "collect"
    if farming then
        if self.reserved_guid then
            self:pursue_target(player, ids, now)
            return
        end
        local corpse = self:find(player, ids, function(object)
            return object:is_unit() and object:is_dead() and object:can_be_looted()
        end, self.config.loot_radius)
        if corpse then
            if self:move(player, corpse:get_position(), self.config.interaction_range, now) then
                self:status("looting", "Looting guide target")
                self:act(now, function() self.api.input.loot_object(corpse, true) end)
            end
            return
        end
    end
    if farming and self:pursue_target(player, ids, now) then return end
    local target = not farming and self:find(player, ids, function(object)
        return not object:is_unit() or not object:is_dead()
    end, self.config.search_radius)
    if target then
        if not self:move(player, target:get_position(), self.config.interaction_range, now) then return end
    elseif next(ids) then
        local position = self:goal_position(player, goal)
        if position and self:move(player, position, 3, now) then
            self:status("waiting", "Waiting for a matching target near the guide point")
        elseif not position and self.state ~= "paused" then self:pause("Target absent and no world position") end
        return
    end
    if kind == "use" then
        local item_id = rule and rule.item_id or goal.item_id
        if not item_id or item_id <= 0 then self:pause("Use goal has no item ID"); return end
        if not target then
            local position = self:goal_position(player, goal)
            if not position then
                if self.state ~= "paused" then self:pause("Untargeted item use needs a guide position") end
                return
            end
            if not self:move(player, position, 3, now) then return end
        end
        self:status("using", "Using guide item; waiting for observed progress")
        self:act(now, function()
            if target then self.api.input.use_item_target(item_id, target)
            else self.api.input.use_item(item_id) end
        end)
    elseif target then
        self:status("interacting", goal.text or kind)
        self:act(now, function()
            if target:is_unit() then self.api.input.interact_with_object(target)
            else self.api.input.use_object(target) end
        end)
    end
end

function Engine:diagnose(player)
    local build = self.api.get_game_build()
    local position = player:get_position()
    local polestar = self.api.addons and self.api.addons.polestar
    local result = string.format("build=%s map=%s xyz=%.2f,%.2f,%.2f", tostring(build and build.build),
        tostring(self.api.get_instance_id()), position.x, position.y, position.z)
    if not polestar or not polestar.is_loaded() then return result .. " Polestar unavailable" end
    local guide, step = polestar.get_guide(), polestar.get_current_step()
    local goal = self:choose_goal(step)
    result = result .. " guide=" .. tostring(guide.id) .. " live_step=" .. tostring(step.num)
    if not goal then return result .. " no required open goal" end
    result = result .. " goal=" .. tostring(goal.type) .. " quest=" .. tostring(goal.quest_id)
        .. " npc=" .. tostring(goal.npc_id) .. " objective=" .. tostring(goal.objective)
    if (goal.type == "kill" or goal.type == "collect") and (goal.npc_id or 0) > 0 then
        local world = self.safety:snapshot(player)
        local candidate, reason = self.safety:choose(player, world, { [goal.npc_id] = true }, {}, self.api.time())
        result = result .. (candidate and " candidate=" .. tostring(candidate.id) or " blocked=" .. tostring(reason))
    end
    return result
end

function Engine:tick()
    if not self.enabled then return end
    local now = self.api.time()
    if now - self.last_tick < self.config.tick_interval then return end
    self.last_tick = now
    local player = self.api.object_manager.get_local_player()
    if not valid(player) then self:pause("Waiting for player; Retry after login"); return end
    if self.config.dry_run then
        self:stop_navigation()
        if now >= (self.next_diagnostic or 0) then
            self.next_diagnostic = now + 5
            self:status("dry-run", self:diagnose(player))
        end
        return
    end
    local build = self.api.get_game_build()
    if not build or build.build ~= 70888 then self:pause("Unsupported client build; expected 70888"); return end
    local navigation = self.navigation()
    if not navigation or not navigation:is_server_available() then
        self:pause(navigation and navigation:get_status() or "Owned navigation unavailable")
        return
    end
    if player:is_dead() or player:is_ghost() then self:pause("Dead or ghost; recover manually, then Retry"); return end
    self.world = self.safety:snapshot(player)
    for guid, expires in pairs(self.cooldowns) do
        if expires <= now then self.cooldowns[guid] = nil end
    end
    if player:is_in_combat() or self.world.engaged > 0 then
        self:stop_navigation()
        self.combat_until = now + self.config.safety.post_combat_delay
        self.was_in_combat = true
        if self.state ~= "paused" then
            local intended = self.reserved_guid and self.world.by_guid[self.reserved_guid]
            local current = player:get_target()
            if not self.selected_guid and intended and intended.engaged and self.target_ids and self.target_ids[intended.id]
                and not intended.player and (not valid(current) or current:get_guid() == intended.guid
                    or not player:can_attack(current)) then
                self.api.input.set_target(intended.object)
                self.selected_guid = intended.guid
            end
            self:status("combat", self.world.engaged > 1
                and "Adds detected; movement stopped, rotation retains combat control"
                or "Movement stopped; rotation retains combat control, no new pulls")
        end
        self.progress_at = now
        return
    end
    if self.was_in_combat then
        self.was_in_combat = false
        self:release_target()
    end
    if self.state == "paused" then return end
    if now < (self.combat_until or 0) then
        self:hold("Waiting for combat to settle before another pull")
        self.progress_at = now
        return
    end
    if player:is_casting_spell() or player:is_channelling_spell() or player:is_on_taxi() then
        self:release_target()
        self:hold("Casting, channeling or on taxi")
        self.progress_at = now
        return
    end
    if player:get_health() / math.max(1, player:get_max_health()) < self.config.minimum_health then
        self:release_target()
        self:hold("Waiting for health before moving or pulling")
        self.progress_at = now
        return
    end
    local polestar = self.api.addons and self.api.addons.polestar
    if not polestar or type(polestar.is_loaded) ~= "function" or not polestar.is_loaded() then
        self:pause("Polestar bridge unavailable; requires core 2.196+ and Polestar")
        return
    end
    local guide = polestar.get_guide()
    if not guide.id or guide.id == "" then self:pause("Load a Polestar guide, then Retry"); return end
    if guide.is_complete then
        self:stop_navigation()
        self:release_target()
        self:status("complete", "Loaded guide complete")
        return
    end
    local step = polestar.get_current_step()
    local goal = self:choose_goal(step)
    if not goal then
        self:stop_navigation()
        self:release_target()
        self:status("waiting", "No required actionable goal; check Polestar")
        return
    end
    local key = table.concat({ guide.id, step.num, goal.index, goal.type, goal.quest_id or 0, goal.objective or 0 }, ":")
    if key ~= self.goal_key then
        self:stop_navigation()
        self:release_target()
        self.goal_key, self.goal = key, goal
        self.attempts, self.next_navigation = 0, 0
        self.navigation_error, self.progress = nil, nil
        self.progress_at = now
        self:status("running", goal.text or goal.type)
        self.api.log(string.format("[TrentNav] guide=%s step=%d goal=%d quest=%d objective=%d type=%s npc=%d item=%d",
            guide.id, step.num, goal.index, goal.quest_id or 0, goal.objective or 0,
            goal.type, goal.npc_id or 0, goal.item_id or 0))
    end
    self.goal = goal
    local current_rule = self:rule(goal)
    local continent_id = goal.continent_id
    if current_rule and current_rule.position then continent_id = current_rule.continent_id end
    if (goal.has_position or (current_rule and current_rule.position)) and continent_id ~= self.api.get_instance_id() then
        self:pause("Goal is on another instance map; travel manually, then Retry")
        return
    end
    local progress = tostring(goal.progress or 0)
    local finished = false
    if (goal.quest_id or 0) > 0 and (goal.objective or 0) > 0 then
        local objective = self.api.quests.get_quest_objectives(goal.quest_id)[goal.objective]
        if objective then
            progress = progress .. ":" .. tostring(objective.fulfilled) .. ":" .. tostring(objective.finished)
            finished = objective.finished
        end
    end
    if (goal.type == "accept" and self.api.quests.is_on_quest(goal.quest_id))
        or (goal.type ~= "accept" and goal.type ~= "turnin" and finished) then
        self:stop_navigation()
        self:release_target()
        self:status("waiting", "Objective observed complete; waiting for Polestar to advance")
        return
    end
    if self.progress ~= progress then
        self.progress, self.progress_at = progress, now
        self.attempts = 0
    end
    if now - self.progress_at > self.config.goal_timeout and not self.destination then
        self:pause("No objective progress; inspect guide or rule, then Retry")
        return
    end
    self:execute(player, goal, now)
end

return Engine
