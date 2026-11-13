<img src="assets/icon.svg" width="64" height="64" alt="">

# TrentNav

**Current milestone:** Sentinel has been removed from the runtime. TrentNav now has its own loopback-only HTTP service, native Detour diagnostic query worker, Lua protocol client and guarded movement follower. **Live navigation is not ready:** the service refuses live routes until collision/liquid coverage and coordinates are validated. The plugin defaults to read-only dry-run mode. See [today’s testing guide](TESTING.md) and [architecture](ARCHITECTURE.md).

Lua questing plugin for **WoW Forever**, using the host API documented in `.api`, Polestar's live guide goals, and TrentNav's owned local navigation stack. No third-party navigation endpoint or Sentinel installation is used.

**This is a host plugin, not a normal WoW addon.** There is intentionally no TOC file. The supplied `.api` files are documentation/type stubs, not implementations: do not load or ship them as the runtime.

## Setup

1. Install Polestar in WoW Forever and load a guide for your character.
2. Use a host/core exposing `core.addons.polestar` (documented as core **2.196+**) and the quest/event functions in the supplied API.
3. Run our local service with the command in [TESTING.md](TESTING.md). It binds only to `127.0.0.1:17654`. Diagnostic terrain routes are not live character routes; health correctly reports `navigation_ready: false` today.
4. Place `header.lua`, `main.lua`, `config.lua` and the `trentnav/` directory together in one plugin directory in your host. Preserve their relative paths. Common modules are supplied by the host.
5. Reload plugins, open **TrentNav - Polestar Questing**, then enable **read-only diagnostics**. It always starts disabled, including after reload. Keep `dry_run = true` for the current test. F9 disables TrentNav. Disable other movement/target-acquisition plugins during future live tests.

## Behavior

- Follows `core.addons.polestar.get_current_step()`, not the arrow for the step you happen to be viewing. Prioritizes the current required open goal, then the first remaining required goal. Optional/along goals are not proactively farmed.
- Requests routes through `trentnav/navigation.lua` and starts its follower only after inspecting an explicitly ready, complete response matching the request, build and physical map. Diagnostic meshes cannot authorize movement. Never falls back to direct movement. Uses instance-map IDs, including **0** for Eastern Kingdoms, not UiMapIDs.
- Accepts and turns in only the exact guide quest ID. Observes `QUEST_DETAIL`, `QUEST_PROGRESS` and `QUEST_COMPLETE` before calling the matching quest action. It does not assume an interaction request actually opened a dialog.
- Handles `goto`, `accept`, `turnin`, `talk`, `interact`, `kill`, `collect` and `use` when the guide supplies sufficient target information. A `complete` goal can infer kill/collect/interact from its live quest objective type **only when an exact target ID is exposed**; otherwise add a rule.
- Reserves exact matching, attackable, untapped NPCs, excluding players, units already fighting, unsafe classifications and overlevel targets. Ranks isolation as well as distance, then observes and reassesses before approaching. Loots matching corpses only when the approach is safe. World objects use `core.input.use_object` rather than corpse looting.
- **Does not cast spells, start/stop auto-attacks, command pets or implement a rotation.** It approaches and selects the reserved quest target; a separate rotation plugin must initiate and perform combat. No built-in healing, food/drink, resurrection, vendor, transport, training or inventory management.
- Lets Polestar observe real progress and advance itself. Never auto-skips a step, ticks a task done, or claims a kill/interaction succeeded merely because an input call returned true.

## Cautious pulling and navigation

All thresholds are configurable in `config.lua`; they are conservative **heuristics**, not exact knowledge of Forever's aggro/social-link mechanics.

- Default: **zero nearby adds** within an 18-yard social radius. Nearby attackable neutral units also count as possible social adds. Moving neighbors get another 6 yards of margin.
- Target must remain safe for 1.5 seconds, be in line of sight, and be on a compatible elevation. Normal/trivial/minus classifications are allowed; elites, rare elites, bosses, rares and unknown classifications are refused by default.
- A reserved GUID stays locked while approaching; new nearer mobs do not cause target hopping. Losing the target, a dangerous patrol, an unsafe route or an 8-second failed rotation handoff causes a 25-second quarantine and reconsideration of other candidates.
- Health must be at least 75%; mana users need at least 35% mana before a new pull. Actual combat, or a visible enemy already fighting you/your pet before your combat flag updates, blocks new pulls and stops owned movement. There is a 3-second settling delay after combat. No blind fleeing or combat chase is attempted: those can pull more mobs.
- A visible hostile's estimated avoidance radius starts at 20 yards, adjusts by level difference, is clamped to 10-35 yards, then gains a 4-yard buffer and another 6 yards if moving. Nearby floors are distinguished using a 6-yard vertical tolerance; this is not a complete multi-floor or patrol-path model.

The navigation sequence is:

1. Polestar supplies the live goal's world-space coordinates. A loaded exact target can replace that point with a near-side stand-off approach, rather than walking through the mob to its center.
2. The owned Lua client requests an ordered origin/destination route from `/v1/route` on loopback, including visible threat zones. The current service returns HTTP 409 because its geometry is not collision-complete. `/v1/diagnostic-route` is a separate inspection endpoint; its result is never accepted for movement.
3. TrentNav checks the returned path **before any walking begins**: each segment, not merely each waypoint, must stay outside the estimated threat envelopes. The endpoint must match. Player position, map, health, target safety and combat state are rechecked after the asynchronous planning response.
4. Only a passing ready path goes to the owned `follow_path`. It uses the supplied `look_at` and forward start/stop API without path smoothing. Before each movement update it checks player/combat state, ownership and live hazards. Service loss, map changes, corridor deviation, long update gaps or stuck movement stop it; there is no blind jumping or direct unstuck recovery. This follower is mock-tested, not in-game verified.
5. There is no direct-walk fallback and no assumption that walkable terrain is safe terrain. Failed/stale plans cannot start movement. Planning has a 12-second timeout; travel retries remain bounded. Missing path-inspection APIs fail closed rather than silently losing safeguards.

Only objects currently loaded by the client can be assessed. Distant spawns, hidden/scripted social links, patrols outside visibility, server latency and another plugin's independent actions can still cause adds. The supplied API has no authoritative linked-pack oracle. Owned tools validate build 70888, but complete coverage, liquids and in-game coordinate verification remain unfinished. Changing `dry_run` to false does not bypass these gates.

## Configuration and objective rules

Edit `config.lua` and reload the plugin. Thresholds, ranges and retry limits are in seconds/yards. The log prints guide, step, goal, quest, objective, NPC and item IDs whenever the active goal changes.

For a quest objective whose target is not exposed by the Polestar bridge, add an explicit rule keyed by **quest ID**, then **objective index**. Index 0 is used for a goal without an objective. The IDs/coordinates below are placeholders, not a verified quest profile:

```lua
objective_rules = {
    [12345] = {
        [1] = {
            action = "kill",
            npc_ids = { 67890, 67891 },
            continent_id = 0,
            position = { x = -1000, y = 500, z = 40 },
        },
    },
},
```

Rule actions are the supported verbs above. For an item collected from a world object use `action = "interact"` and the exact object entry IDs in `npc_ids` (read through the host's `get_npc_id()`; confirm its behavior in-game). For a quest item use `action = "use"`, `item_id`, and optionally target IDs. Without a target, the item is used at the guide/rule point. Ground-target reticles and arbitrary gossip branches are **not** automated. A rule position overrides the guide point and must include `continent_id`; provide a known world-space height. UI-map percentages are not world coordinates.

Rewards with no choices or one choice are automatic. With multiple choices, choose manually or set a 1-based choice in `reward_choices`, for example `[12345] = 2`. No item is chosen by guessed value or suitability.

Set `engage_range` for your rotation: the default is 4 yards, not a class-aware spell range. The rotation plugin must be able to initiate combat on an explicitly selected target; a rotation that runs only after combat begins may need a manual pull. Disable its independent target acquisition, chain pulling, movement and uncontrolled AoE/cleave/pet pulls. Those actions can bypass TrentNav's single-target decision. TrentNav does not clear or replace an existing hostile combat target. If the reserved mob body-pulls first and there is no hostile target selected, it hands that exact mob to the rotation without chasing. The old `config.combat` hook and auto-attack helper dependency have been removed.

## Pauses and limitations

The menu shows the reason for a pause. Explicit pauses remain stopped until **Retry current goal** or disable/re-enable. Temporary safety holds are reassessed automatically, with unsafe/unreachable targets temporarily quarantined. A missing server, missing addon, wrong quest panel, multiple reward choices, unknown height, missing target IDs, unsupported action, death, cross-map destination, repeated path failure or stalled objective can stop automation. Low health, low mana before pulls, casting/channeling, taxi movement and post-combat settling temporarily suspend actions and resume automatically when clear.

After a reload, close and reopen any quest panel: the plugin must observe its panel-stage event. After a manual reward choice or manual objective completion, allow Polestar to advance; Retry if it is still paused. Required tasks, escorts, scripted events, special item targeting, travel and service goals need manual handling or additional explicit logic. Collection with a mob ID means killing/looting that mob, not guessing which object or creature drops an item.

**This is not a verified unattended 1-60 bot or a complete database of Forever quests.** The executor implements the supported goals described above, but live execution is currently blocked by the navigation readiness gate. Ambiguous goals require configuration rather than farming arbitrary nearby units. Once geometry passes the remaining gates, test short routes under supervision. Automation may violate your server's rules and can put your account at risk.

## Integration references

The implementation calls the supplied host API and our own loopback service; it does not read WoW addon globals directly or execute arbitrary Lua in the game. The Sentinel links below are historical references only, not runtime dependencies.

- Local contract: `.api/core.lua` (Polestar, quests, input, events, HTTP), `.api/game_object.lua`, and common vector/menu modules.
- [SentinelNavClient](https://github.com/TheSentinelCore/SentinelCore/tree/d0208c9c9c53f5c52d84a21d8b9136925bc7f5d4/SentinelNavClient): verified singleton export, ordered `plan_route` options, avoid-zone serialization and `follow_path`/`stop` contracts against upstream source.
- [SentinelNavServer](https://github.com/TheSentinelCore/SentinelCore/tree/d0208c9c9c53f5c52d84a21d8b9136925bc7f5d4/SentinelNavServer) and [Emulators](https://github.com/TheSentinelCore/SentinelCore/tree/d0208c9c9c53f5c52d84a21d8b9136925bc7f5d4/Emulators): external navigation infrastructure, not bundled or modified here.
- [Polestar engine](https://github.com/fitzforge/polestar/blob/19bd636e7d6f33231770f6029c76d2d4dfc63798/addon/Polestar/Core/Engine.lua): checked live/shown step behavior and goal views. Its [in-addon navigation API](https://github.com/fitzforge/polestar/blob/19bd636e7d6f33231770f6029c76d2d4dfc63798/addon/Polestar_Nav/API.lua) is a different router; TrentNav uses neither Sentinel nor `Polestar_NavEngine`.

Research checked November 12, 2026. Mock validation cannot verify actual Forever target IDs, meshes, object entry behavior, class combat effectiveness, host loader compatibility or in-game event timing. An in-game smoke test is still required.
