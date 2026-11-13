# Testing TrentNav today

## What this build can test

This is a **read-only integration and extraction milestone**, not a ready unattended quest bot. The runtime no longer uses Sentinel. Our own localhost service can query a combined terrain/building/doodad diagnostic mesh with conservative liquid exclusions, but surrounding coverage and world-coordinate validation are unfinished. Live route requests are deliberately rejected, even if you change the Lua dry-run setting.

No files in the WoW installation have been changed. No game client, injector or rotation plugin was launched by the development tools.

## 1. Start our service

In PowerShell, from the project folder:

```powershell
.\tools\Start-NavService.ps1 -DiagnosticMesh .local/combined-diagnostic-doodads/combined.tnavdiag
```

Keep that terminal open. Ctrl+C stops it. Omit `-DiagnosticMesh` to test the transport without loading assets. There is no firewall/router forwarding or external server configuration. The process binds only to `127.0.0.1`; no runtime downloads or navigation calls leave this service.

In a second PowerShell window:

```powershell
$health = Invoke-RestMethod http://127.0.0.1:17654/v1/health
$health | ConvertTo-Json -Depth 6
$request = @{ start = $health.sample.start; end = $health.sample.end } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:17654/v1/diagnostic-route -Method Post -ContentType application/json -Body $request
```

Expected: health says `diagnostic_loaded: true`, `navigation_ready: false`; the query says `complete: true`, `diagnostic: true`, `navigation_ready: false`. This proves a complete path **within the diagnostic surface only**, not collision-safe traversal. The sample coordinates belong to the selected tile and are not a destination to walk your character to.

## 2. Load the Lua plugin

1. Run `.\tools\Stage-Plugin.ps1` to produce a fresh package under `.local/plugin-bundles`. It copies only the six runtime Lua files and adds a hash manifest and this guide. It does not include game data or the service binaries.
2. Copy its `TrentNav` folder into your host’s plugin directory, preserving `trentnav/` beneath it. This is not a normal WoW addon and does not go in `Interface/AddOns`. The host’s plugin-directory location has not been established; no automatic deployment is attempted.
3. Use the supplied API-compatible host/core with the Polestar bridge (documented as core 2.196+), and load a Polestar guide in WoW Forever.
4. Leave `dry_run = true` in `config.lua`. Reload plugins; TrentNav should start disabled.
5. Open **TrentNav - Polestar Questing** and click **Log test snapshot (read-only)**. Then enable **read-only diagnostics**.
6. Expected log: build `70888`, your physical map ID, world XYZ, guide ID, **live** step, goal type and quest/NPC/objective IDs. Kill/collect goals with an exact NPC ID also report the current conservative candidate or refusal reason. No target selection, quest interaction, item use, combat or movement should occur.
7. Page to a different *displayed* Polestar step. The diagnostic must continue reporting the live step, not that preview. Load another guide and verify the diagnostic updates.
8. Press **F9**: the enable box should turn off. Reload: it should remain disabled. Stop the service: its status should become unavailable; restarting it should restore the explicit incomplete-coverage status.

The expected message **Live navigation blocked: collision_coverage_incomplete** is intentional, not a reason to install Sentinel or disable a safeguard. Other plugins can still perform their own actions; disable those during this read-only test if you want an unambiguous observation.

## 3. Bring back useful observations

- The `[TrentNav]` snapshot lines and any exact Lua exception, with its file/line.
- The loaded Polestar guide, live step and what you expected that step to do.
- Physical map ID and world XYZ from a few known outdoor locations, with the in-game place names. Do not substitute UI map percentages for world coordinates.
- Whether changing guides, reloading and F9 behave as described.
- Any unsafe candidate reported by the diagnostic. Nearby units are risk estimates, not confirmed server-linked packs.

## Implemented safeguards and remaining gates

The Lua client validates protocol/build/map/request identity, endpoint matching, finite bounded coordinates, route length and waypoint count. Planning alone cannot move; cancellation invalidates late callbacks. The follower requires a validated path and live guard, checks combat/death/taxi/casting/falling/swimming, route deviation, frame gaps, no-progress timeout and service freshness. It does not smooth corners, jump out of obstacles or fall back to direct walking. Follower behavior is mocked, not yet tested in game.

Before a live questing test we still need complete placed-model/building and liquid coverage, validated placement transforms and coordinates, production mesh provenance/agent settings/tile connections, a live-ready service implementation, and supervised short walking tests. Transport/doors/boats, death recovery, vendors/repair/inventory management, escorts and ambiguous scripted objectives remain unsupported. Rotation logic stays in your separate plugin; auto-target/cleave/pet acquisition in that plugin can defeat single-pull safeguards.

## Collision extraction milestone

The bounded extraction reads 746 M2/WMO assets. Direct placements plus 6,430 active embedded WMO instances produce 1,026,900 vertices and 1,176,651 collision triangles. The combined, tile-clipped diagnostic has 78,153 vertices and 141,524 triangles and compiles to 3,514 walkable polygons. All 3,514 polygons were checked against 44 conservative full-height liquid exclusion columns, with zero positive-area intersections. These columns also block bridges above water; they do not implement swimming or liquid classification. The latest artifacts and verification receipt are under `.local/combined-diagnostic-doodads`. Surrounding coverage and in-game coordinate verification remain blockers. See `tools/COLLISION.md`.

## Local validation recorded

- Native Release build passes with `/W4 /WX` for owned C++ targets.
- The combined diagnostic mesh passes the owned query worker and HTTP service route; live routes still return HTTP 409.
- 20 geometry/liquid checks and five native scene checks cover clipping, malformed data, liquid/wall detours, disconnected floors, low ceilings and invalid exclusion bounds.
- 16 HTTP/service checks pass, including invalid input, wrong origins/hosts, overlarge payloads, nonfinite coordinates and live-route refusal.
- Four malformed diagnostic artifact checks reject invalid data.
- 49 checks pass independently on Lua 5.1 and Lua 5.4: 37 navigation/engine cases, nine main-loader/dry-run/F9 checks and three late-callback/approval-expiry checks. These cover cancellation, invalid/diagnostic routes, hazards, player states, timeout, no-input dry runs and unload behavior.

These tests do not establish in-game correctness. Automation may violate server rules; run only where authorized.
