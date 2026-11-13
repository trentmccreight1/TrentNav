local Safety = {}
Safety.__index = Safety

local function distance(first, second)
    local delta_x, delta_y, delta_z = first.x - second.x, first.y - second.y, first.z - second.z
    return math.sqrt(delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)
end

local function point_segment(point, first, second)
    local delta_x, delta_y, delta_z = second.x - first.x, second.y - first.y, second.z - first.z
    local length_squared = delta_x * delta_x + delta_y * delta_y + delta_z * delta_z
    local amount = 0
    if length_squared > 0 then
        amount = math.max(0, math.min(1, ((point.x - first.x) * delta_x
            + (point.y - first.y) * delta_y + (point.z - first.z) * delta_z) / length_squared))
    end
    local closest = { x = first.x + amount * delta_x, y = first.y + amount * delta_y,
        z = first.z + amount * delta_z }
    return distance(point, closest), math.abs(point.z - closest.z)
end

function Safety.new(api, config)
    return setmetatable({ api = api, config = config, settings = config.safety }, Safety)
end

function Safety:radius(player_level, unit)
    local settings = self.settings
    local radius = settings.maximum_aggro_radius
    if unit.level > 0 then
        radius = math.max(settings.minimum_aggro_radius,
            math.min(settings.maximum_aggro_radius, settings.aggro_radius + unit.level - player_level))
    end
    return radius + settings.aggro_buffer + (unit.moving and settings.patrol_buffer or 0)
end

function Safety:snapshot(player)
    local origin = player:get_position()
    local world = { origin = { x = origin.x, y = origin.y, z = origin.z },
        level = player:get_level(), units = {}, by_guid = {}, engaged = 0 }
    local player_guid = player:get_guid()
    local pet = player:get_pet()
    local pet_guid = pet and pet:is_valid() and pet:get_guid()
    for _, object in ipairs(self.api.object_manager.get_all_objects()) do
        if object:is_valid() and object:is_unit() and not object:is_dead() and player:can_attack(object) then
            local position = object:get_position()
            local target = object:get_target()
            local target_guid = target and target:is_valid() and target:get_guid()
            local unit = { object = object, guid = object:get_guid(), id = object:get_npc_id(),
                position = { x = position.x, y = position.y, z = position.z },
                level = object:get_level(), classification = object:get_classification(),
                hostile = player:is_enemy_with(object), moving = object:is_moving(),
                combat = object:is_in_combat(), player = object:is_player(),
                tapped = object:is_tap_denied() ~= 0 }
            unit.radius = self:radius(world.level, unit)
            unit.engaged = unit.combat and target_guid and (target_guid == player_guid or target_guid == pet_guid) or false
            world.units[#world.units + 1] = unit
            if unit.guid and unit.guid ~= "" then world.by_guid[unit.guid] = unit end
            if unit.engaged then
                world.engaged = world.engaged + 1
            end
        end
    end
    return world
end

function Safety:approach(world, target)
    local position = target.position
    local delta_x, delta_y = world.origin.x - position.x, world.origin.y - position.y
    local length = math.sqrt(delta_x * delta_x + delta_y * delta_y)
    local margin = math.max(0, self.config.engage_range - 1)
    if length <= margin then return world.origin end
    return { x = position.x + delta_x / length * margin,
        y = position.y + delta_y / length * margin, z = position.z }
end

function Safety:segment_clear(world, first, second, ignore_guid)
    for _, unit in ipairs(world.units) do
        if unit.hostile and (not ignore_guid or unit.guid ~= ignore_guid) then
            local separation, vertical = point_segment(unit.position, first, second)
            if vertical <= self.settings.vertical_tolerance and separation < unit.radius then
                return false, "Route enters estimated aggro range of " .. tostring(unit.id)
            end
        end
    end
    return true
end

function Safety:path_clear(world, path, first_index, ignore_guid, horizon)
    if type(path) ~= "table" or #path == 0 then return false, "Navigation returned no inspectable path" end
    local previous, travelled = world.origin, 0
    for index = first_index or 1, #path do
        local point = path[index]
        if not point or type(point.x) ~= "number" or type(point.y) ~= "number" or type(point.z) ~= "number"
            or point.x ~= point.x or point.y ~= point.y or point.z ~= point.z
            or math.abs(point.x) == math.huge or math.abs(point.y) == math.huge or math.abs(point.z) == math.huge then
            return false, "Navigation returned an invalid waypoint"
        end
        local segment_length = distance(previous, point)
        local endpoint = point
        if horizon and travelled + segment_length > horizon and segment_length > 0 then
            local fraction = (horizon - travelled) / segment_length
            endpoint = { x = previous.x + (point.x - previous.x) * fraction,
                y = previous.y + (point.y - previous.y) * fraction,
                z = previous.z + (point.z - previous.z) * fraction }
        end
        local clear, reason = self:segment_clear(world, previous, endpoint, ignore_guid)
        if not clear then return false, reason end
        travelled = travelled + segment_length
        if horizon and travelled >= horizon then break end
        previous = point
    end
    return true
end

function Safety:assess(player, world, unit, ids)
    if not unit or not unit.guid or unit.guid == "" or not ids[unit.id] then return false, "Not an exact quest target" end
    if unit.player or unit.tapped or unit.combat then return false, "Target is owned, fighting or a player" end
    if unit.level <= 0 or unit.level > world.level + self.config.max_level_difference then
        return false, "Target level is unsafe or unknown"
    end
    if not self.settings.allowed_classifications[unit.classification] then
        return false, "Elite, rare, boss or unknown classification"
    end
    local target_distance = distance(world.origin, unit.position)
    if target_distance > self.config.search_radius then return false, "Target outside search radius" end
    if math.abs(world.origin.z - unit.position.z) > self.settings.vertical_tolerance then
        return false, "Target is on another elevation"
    end
    if not self.api.graphics.is_line_of_sight(player, unit.object) then return false, "Target not in line of sight" end
    local adds, clearance = 0, self.config.search_radius
    for _, neighbor in ipairs(world.units) do
        if neighbor.guid ~= unit.guid and math.abs(neighbor.position.z - unit.position.z) <= self.settings.vertical_tolerance then
            local separation = distance(neighbor.position, unit.position)
            local social_radius = self.settings.social_radius + (neighbor.moving and self.settings.patrol_buffer or 0)
            if separation < social_radius then adds = adds + 1 end
            clearance = math.min(clearance, separation)
        end
    end
    if adds > self.settings.max_nearby_adds then return false, "Nearby pack or patrol: " .. adds .. " potential adds" end
    local approach = self:approach(world, unit)
    local clear, reason = self:segment_clear(world, approach, approach, unit.guid)
    if not clear then return false, reason end
    return true, nil, target_distance - math.min(clearance, 60) * 0.6 + (unit.moving and 8 or 0)
end

function Safety:choose(player, world, ids, cooldowns, now)
    local best, best_score, last_reason
    for _, unit in ipairs(world.units) do
        if ids[unit.id] and (cooldowns[unit.guid] or 0) <= now then
            local safe, reason, score = self:assess(player, world, unit, ids)
            if safe and (not best_score or score < best_score) then best, best_score = unit, score end
            if not safe then last_reason = reason end
        end
    end
    return best, last_reason or "No safe isolated quest target is currently visible"
end

function Safety:avoid_zones(world, ignore_guid)
    local zones = {}
    for _, unit in ipairs(world.units) do
        if unit.hostile and (not ignore_guid or unit.guid ~= ignore_guid) then
            zones[#zones + 1] = { x = unit.position.x, y = unit.position.y, z = unit.position.z,
                radius = unit.radius, cost = 1000 }
        end
    end
    return zones
end

return Safety
