local Navigation = {}
Navigation.__index = Navigation

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) <= 100000
end

local function point(value)
    return value and finite(value.x) and finite(value.y) and finite(value.z)
end

local function distance(first, second)
    local delta_x, delta_y, delta_z = first.x - second.x, first.y - second.y, first.z - second.z
    return math.sqrt(delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)
end

local function lines(body)
    if type(body) ~= "string" or #body > 524288 or body:find("\r") or body:find("%z") then return nil end
    local result = {}
    for line in (body .. (body:sub(-1) == "\n" and "" or "\n")):gmatch("([^\n]*)\n") do
        result[#result + 1] = line
    end
    return result
end

local function coordinates(position)
    return string.format("%.9g %.9g %.9g", position.x, position.y, position.z)
end

local function segment_distance(position, first, second)
    local delta_x, delta_y, delta_z = second.x - first.x, second.y - first.y, second.z - first.z
    local length = delta_x * delta_x + delta_y * delta_y + delta_z * delta_z
    local amount = length > 0 and math.max(0, math.min(1, ((position.x - first.x) * delta_x
        + (position.y - first.y) * delta_y + (position.z - first.z) * delta_z) / length)) or 0
    return distance(position, { x = first.x + delta_x * amount, y = first.y + delta_y * amount,
        z = first.z + delta_z * amount })
end

function Navigation.new(api, settings, vector)
    assert(type(settings.port) == "number" and settings.port % 1 == 0 and settings.port >= 1024 and settings.port <= 65535,
        "Navigation port must be an integer between 1024 and 65535")
    return setmetatable({ api = api, settings = settings, vector = vector, generation = 0, request_id = 0,
        health_generation = 0, next_health = 0, health_at = -math.huge, available = false,
        message = "Local service not checked", endpoint = "http://127.0.0.1:" .. settings.port }, Navigation)
end

function Navigation:is_server_available()
    return not self.closed and self.available and self.api.time() - self.health_at <= self.settings.health_ttl
end

function Navigation:get_status()
    return self.message
end

function Navigation:refresh_health()
    local now = self.api.time()
    if self.closed then return end
    if self.health_pending and now - self.health_pending < self.settings.request_timeout then return end
    if now < self.next_health then return end
    self.health_generation = self.health_generation + 1
    local generation = self.health_generation
    self.health_pending, self.next_health = now, now + self.settings.health_interval
    self.api.http_post(self.endpoint .. "/v1/health", { Accept = "text/plain" }, "", function(code, content_type, body)
        if self.closed or self.health_generation ~= generation then return end
        self.health_pending = nil
        self.health_at = self.api.time()
        local response = lines(body)
        local compatible = code == 200 and type(content_type) == "string" and content_type:match("^text/plain")
            and response and #response == 4 and response[1] == "TNAV1 HEALTH" and response[2] == "70888"
        self.available = compatible and response[3] == "1" and response[4] == "ready" or false
        self.message = self.available and "Local service ready"
            or (compatible and "Live navigation blocked: " .. response[4] or "Local service unavailable or incompatible")
        if not self.available and self.path then self:finish(false, self.message) end
    end)
end

function Navigation:stop()
    self.generation = self.generation + 1
    self.path, self.callback, self.pending, self.approved_path = nil, nil, nil, nil
    self.index = 1
    if self.moving then
        self.moving = false
        self.api.input.move_forward_stop()
    end
end

function Navigation:close()
    self.closed = true
    self.health_generation = self.health_generation + 1
    self:stop()
end

function Navigation:finish(success, reason)
    local callback = self.callback
    self:stop()
    if callback then callback(success, reason) end
end

function Navigation:plan_route(endpoints, callback, options)
    self:stop()
    if not self:is_server_available() then callback(false, { reason = self.message }); return end
    if type(endpoints) ~= "table" or #endpoints ~= 2 or not point(endpoints[1]) or not point(endpoints[2]) then
        callback(false, { reason = "Invalid route endpoints" }); return
    end
    local build = self.api.get_game_build()
    local map_id = self.api.get_instance_id()
    if not build or build.build ~= 70888 or type(map_id) ~= "number" or map_id % 1 ~= 0 or map_id < 0
        or not options or options.map_id ~= map_id or options.allow_partial ~= false then
        callback(false, { reason = "Build, map or complete-route policy mismatch" }); return
    end
    local zones = options.avoid_zones or {}
    if #zones > 256 then callback(false, { reason = "Too many hazards for bounded route request" }); return end
    self.request_id = self.request_id + 1
    local request_id = tostring(self.request_id)
    local request = { "TNAV1 REQUEST", request_id, "70888", tostring(map_id), "2", coordinates(endpoints[1]),
        coordinates(endpoints[2]), tostring(#zones) }
    for _, zone in ipairs(zones) do
        if not point(zone) or not finite(zone.radius) or zone.radius <= 0 or zone.radius > 200 then
            callback(false, { reason = "Invalid avoid zone" }); return
        end
        request[#request + 1] = coordinates(zone) .. " " .. string.format("%.9g", zone.radius)
    end
    local generation = self.generation
    self.pending = { at = self.api.time(), callback = callback }
    self.api.http_post(self.endpoint .. "/v1/route", { ["Content-Type"] = "text/plain", Accept = "text/plain" },
        table.concat(request, "\n") .. "\n", function(code, content_type, body)
            if self.closed or self.generation ~= generation or not self.pending then return end
            local expired = self.api.time() - self.pending.at >= self.settings.request_timeout
            self.pending = nil
            local response = lines(body)
            local count = response and tonumber(response[6])
            local valid = not expired and code == 200 and type(content_type) == "string" and content_type:match("^text/plain")
                and response and response[1] == "TNAV1 ROUTE" and response[2] == request_id
                and response[3] == "70888" and response[4] == tostring(map_id) and response[5] == "READY"
                and count and count % 1 == 0 and count >= 2 and count <= 4096 and #response == 6 + count
                and self.api.get_instance_id() == map_id and self:is_server_available()
            local path, total = {}, 0
            if valid then
                for index = 1, count do
                    local coordinate_x, coordinate_y, coordinate_z = response[index + 6]:match("^(%S+) (%S+) (%S+)$")
                    local waypoint = { x = tonumber(coordinate_x), y = tonumber(coordinate_y), z = tonumber(coordinate_z) }
                    if not point(waypoint) then valid = false; break end
                    if index > 1 then
                        local length = distance(path[index - 1], waypoint)
                        total = total + length
                        if length > self.settings.max_segment_length or total > self.settings.max_route_length then valid = false; break end
                    end
                    path[index] = self.vector.new(waypoint.x, waypoint.y, waypoint.z)
                end
                valid = valid and distance(path[1], endpoints[1]) <= self.settings.endpoint_tolerance
                    and distance(path[#path], endpoints[2]) <= self.settings.endpoint_tolerance
            end
            if not valid then callback(false, { reason = "Refused missing, partial, diagnostic or incompatible route" }); return end
            self.approved_path, self.approved_map, self.approved_at = path, map_id, self.api.time()
            callback(true, { waypoints = path })
        end)
end

function Navigation:follow_path(path, callback)
    if path ~= self.approved_path or not self:is_server_available() or self.api.get_instance_id() ~= self.approved_map
        or self.api.time() - self.approved_at > 2 then callback(false, "Path was not approved or approval expired"); return end
    self.path, self.callback, self.index = path, callback, 2
    self.approved_path = nil
    self.last_progress, self.started_at, self.last_frame = self.api.time(), self.api.time(), self.api.time()
    self.best_distance = math.huge
end

function Navigation:get_current_path() return self.path end
function Navigation:get_path_index() return self.index or 1 end

function Navigation:tick(allowed, guard)
    if self.closed then return end
    self:refresh_health()
    local now = self.api.time()
    if self.pending and now - self.pending.at >= self.settings.request_timeout then
        local callback = self.pending.callback
        self:stop()
        callback(false, { reason = "Local route request timed out" })
    end
    if not self.path then return end
    local player = self.api.object_manager.get_local_player()
    if not allowed or not self:is_server_available() or not player or not player:is_valid()
        or player:is_dead() or player:is_ghost() or player:is_in_combat() or player:is_on_taxi()
        or player:is_casting_spell() or player:is_channelling_spell() or player:is_falling() or player:is_swimming()
        or self.api.get_instance_id() ~= self.approved_map then
        self:finish(false, "Movement ownership, player state or local service changed"); return
    end
    if now - self.last_frame > self.settings.frame_gap or now - self.started_at > self.settings.route_timeout then
        self:finish(false, "Movement update gap or route timeout"); return
    end
    self.last_frame = now
    local position = player:get_position()
    if not point(position) then self:finish(false, "Invalid player position"); return end
    if type(guard) ~= "function" then self:finish(false, "Live safety guard missing"); return end
    local safe, reason = guard(player, self.path, self.index)
    if not safe then self:finish(false, reason or "Live route unsafe"); return end
    local target = self.path[self.index]
    if segment_distance(position, self.path[self.index - 1], target) > self.settings.corridor_tolerance then
        self:finish(false, "Player left approved route; no direct recovery"); return
    end
    local remaining = distance(position, target)
    if remaining <= self.settings.waypoint_tolerance then
        if self.index == #self.path then self:finish(true, "Arrived"); return end
        self.index = self.index + 1
        self.best_distance, self.last_progress = math.huge, now
        target = self.path[self.index]
        remaining = distance(position, target)
    end
    if remaining < self.best_distance - 0.15 then self.best_distance, self.last_progress = remaining, now end
    if now - self.last_progress > self.settings.stuck_timeout then
        self:finish(false, "No movement progress; no blind jumping or unstuck motion"); return
    end
    self.api.input.look_at(target)
    if not self.moving then
        self.moving = true
        self.api.input.move_forward_start()
    end
end

return Navigation
