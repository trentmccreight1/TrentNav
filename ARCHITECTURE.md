# TrentNav: independent questing architecture

Research checked November 12, 2026. This is a proposed implementation plan, not a claim that the systems below already exist.

Local client inspection has since confirmed build `1.60.1.70888`, product `wowf`, and the shared parent data directory. An owned offline probe, terrain exporter, direct-placement collision exporter and diagnostic Recast/Detour compiler/query now work on one actual tile; see [CLIENT_DATA.md](CLIENT_DATA.md) and [tools/README.md](tools/README.md). Complete collision extraction, production meshes and verified live navigation remain pending; the owned runtime transport/client/follower are implemented but readiness-gated.

## Status and scope

The Lua runtime no longer uses Sentinel. An owned loopback HTTP service, native diagnostic query worker, Lua client and guarded follower are implemented. The service refuses live routes while geometry coverage is incomplete; the plugin defaults to read-only diagnostics. Sentinel was supplied as a reference, not a runtime dependency. Its client, server, mesh format and hosted navigation are not required.

TrentNav owns quest decisions, target selection, navigation, and supervision. A separate plugin owns combat rotations. Intelligence means reliable observation, explicit action confirmation, conservative uncertainty handling, and bounded recovery, not random delays or promises of human-equivalent play. No anti-cheat evasion is proposed.

## 1. Our navigation stack

Current boundary: owned offline geometry tools and a Python standard-library loopback service invoking an owned C++ Detour query worker, plus an owned Lua client and movement follower. Recast/Detour is a general-purpose library, not another bot/navigation service. This still uses third-party library code; writing polygon generation and pathfinding entirely from scratch would be a separate scope decision. Sentinel and Polestar Nav are not fallback routers. Production tiled meshes and ready-route serving remain acceptance-gated.

Pipeline:

1. Identify the exact client using `core.get_game_build()`, the realm/profile revision, and physical instance-map IDs. A private server name or nominal expansion does not prove geometry compatibility.
2. Read compatible, user-provided local world assets through a separate offline tool. Extract terrain triangles, placed world/doodad collision, liquids, interiors, and connections. No full-world geometry export was found in the supplied Lua API; this is a real prerequisite, not something HTTP or raycasts solve. Verify the client asset format before selecting or implementing a decoder.
3. Build tiled walkable meshes with player radius/height, slope, clearance, step height, liquid permissions, and fall restrictions. Version by asset fingerprint, compiler settings, and mesh schema. Include ground-floor/bridge/cave separation. Do not ship or assume somebody else's map pack matches Forever.
4. Use Detour polygon-corridor queries and straight-path extraction inside our service. Validate endpoint projection, elevation, connected component, complete reachability, and query status; reject partial/truncated/node-budget-exhausted results. Long routes need tile streaming and a higher-level regional graph.
5. Layer static hazards and temporary threat costs/exclusions onto the query. Implement and test polygon-aware filtering/cost integration; Detour does not automatically understand WoW mobs or arbitrary threat circles. Invalidate affected paths when hazard revisions change.
6. Follow the corridor through our Lua module using documented `core.input.look_at`, movement start/stop calls, and live position feedback. Steering, stop distance, segment projection, speed limits, and recovery belong to us. No arbitrary spline smoothing that can cut corners outside the corridor.
7. Revalidate a bounded lookahead against current units and collision probes. Stop before an unsafe segment and request another route; never replace a rejected route with straight-line movement.

`core.http_post()` provides asynchronous transport to our local service. Protocol fields should include request/generation IDs, build and mesh IDs, instance/map and coordinate frame, agent profile, origin/destination, observed threats, and expiration. Responses need explicit status, matching IDs/revisions, a complete corridor/waypoints, endpoint errors, and applicable travel links. Validate finite bounded coordinates, payload sizes, waypoint counts, map/build match, origin drift, and path age. Late callbacks cannot move a stopped, reloaded, teleported, or newly fighting player. Bind the service to loopback, restrict callers, and use a per-install authentication token; do not expose arbitrary file access or command execution.

Local raycasts and height probes are useful vetoes and movement checks. They do not prove that a far route exists, distinguish every overhang from walkable ground, or guarantee future doors/terrain are passable. Unknown probe outcomes should stop or request more evidence.

Transport is a separate verified graph: walking entrances, taxi nodes, boats/zeppelins, elevators, portals, and explicit interaction links. A link needs prerequisites, execution, timeout, and post-transition map/position confirmation. Never join disconnected maps with a straight line. Initially support ground walking only; unsupported swimming, transport, flying, or platform movement pauses explicitly.

## 2. Linked packs: knowledge versus inference

Primary sources show more than one mechanism: AzerothCore formation flags distinguish member-assists-leader from leader-assists-member; TrinityCore evaluates formation membership on engagement and also has ordinary assistance/help logic; CMaNGOS has directional creature-linking flags. These prove possible server-side mechanisms, not which engine, settings, scripts, or data Forever actually uses. Custom encounter scripts can add further behavior. [S2-S4]

The supplied client API exposes object identity, entry, position, faction, combat state, target, and threat observations. No documented formation ID, linked-spawn list, or authoritative pre-pull assist graph was found. `get_threat_situation()` is not a pre-combat pack oracle. Forever GUIDs are documented as obfuscated, so do not decode server spawn IDs from GUID suffixes.

Represent pack relationships as directed evidence edges, not just proximity clusters:

| Evidence | Interpretation | Conservative use |
| --- | --- | --- |
| Verified matching server export or curated tested profile | A specific link with provenance and compatible revision | Apply its direction/conditions and transitive closure; account for currently unseen members |
| Repeated clean observations of co-engagement | Suspected assistance/link, confidence and context recorded | Penalize or avoid; do not promote to authoritative fact |
| Nearby compatible creatures, shared movement, approaching patrol | Potential social/body adds | Estimate reachable/predicted exposure; never call this a confirmed hard link |
| Unknown region, incomplete visibility, stale spawn match | Missing evidence | Increase uncertainty or refuse the pull; absence from the object list is not absence from the world |

Persistent identity needs build/profile, physical map, NPC entry, position/region and spawn/patrol matching with ambiguity handling. NPC entry alone is not a spawn identity. Live GUIDs are session-scoped observations, not assumed stable database keys. Keep evidence timestamp, origin, positive/negative observations, confidence, and invalidation reason. Patrols and respawns make position-only matches unreliable.

Learn passively from normal engagements and reliable target/threat changes where available. Do not deliberately pull unsafe packs to learn. Co-aggro can instead be caused by body proximity, AoE, a pet, another player, or a scripted wave. An isolated kill does not disprove a directional/conditional link. Record ambiguous observations as ambiguous.

## 3. Pull planning, not nearest-target selection

For each exact quest target, evaluate the entire approach, engagement area, likely combat duration, loot approach, and safe continuation. Estimate a plausible additional-attacker set from known-link closure, possible assistance, moving patrols, uncertainty, respawn exposure, vertical/LOS relationships, and rotation behavior. The estimate is not a guarantee.

Hard gates first: correct objective, alive/attackable/untapped target, supported classification, sufficient resources, safe route, and no known link exceeding the configured pull budget. Default to one planned enemy and reject suspected multi-pulls. Unknown dangerous encounters remain blocked until configured or observed safely; do not silently raise the budget after waiting.

Then rank alternatives by objective progress versus travel time, likely damage/resource use, add exposure, recovery cost, and uncertainty. Prefer a genuinely isolated alternative or wait for a patrol window. Recheck during approach and immediately before handing off the target. Stable observations are helpful but do not prove safety.

Predict patrol envelopes only over short, confidence-limited horizons. Learn running-away/help behavior and approximate kill durations from observations, with conservative cold-start defaults. No universal 20-yard assist radius, equal-level strength assumption, or permanent safe-patrol timing. Include casters/runners, elites, pets, leash/reset ambiguity, and dangerous terrain in profile risk.

A safe retreat needs its own validated route and movement ownership. Do not assume running backward resets combat, drag enemies through unobserved areas, or issue panic jumps. If another plugin owns combat movement, TrentNav must request handoff rather than fight it. Without a supported handoff, stop new pulls and alert; this cannot guarantee rescue from an existing fight.

## 4. Rotation integration contract

Selecting a target alone cannot enforce one-enemy combat if the rotation independently switches targets, cleaves, commands a pet, or moves. Add an explicit adapter with readiness, target GUID, allowed initiation, movement owner, pause/yield acknowledgment, and observed engagement/result. Capability checks must be honest: an unsupported rotation cannot be assumed to obey these controls.

No spell rotation, spell selection, interrupts, pet commands, or damage logic belongs in TrentNav. Rotation readiness and engagement reach are capability inputs, not guessed class rules. A rotation that only acts after combat begins cannot be treated as able to initiate a pull. Do not introduce body-pulling as an unreviewed workaround.

## 5. Other systems required for a capable quester

| System | Required behavior and failure handling |
| --- | --- |
| World model | Timestamped snapshots, object validity, floor/map context, visibility limits, freshness checks, and event/scan reconciliation |
| Quest planner | Polestar live required goals plus actual quest state; dependencies, prerequisites, chains, log capacity, party context and objective feasibility; do not reorder required guide steps without an explicit policy |
| Transactional actions | Observe before/after state for accepting, talking, using, collecting, looting, and rewarding; input success is not objective success; timeouts, idempotent retry, and exact NPC/object/item/quest identity |
| Special objectives | Explicit handlers for gossip branches, quest-item targets/reticles, escorts, timed events, waves, vehicles and defense; pause when unsupported instead of farming nearby mobs |
| Spawn search | Bounded search areas, observed death/respawn history, contention/tap checks, inaccessible-spawn quarantine, and alternatives; no infinite camping or guessed drop source |
| Logistics | Bag slots, protected items, quest-item preservation, unique-item constraints, repair/durability, supplies and money reserves; verified service NPCs, safe detours and confirmed transactions |
| Rest and readiness | Health/mana, food/drink availability and confirmation, debuffs and post-combat settling through an agreed noncombat owner; do not duplicate rotation healing |
| Death recovery | Observe death/ghost/corpse state, safe corpse route, resurrection availability/delay and hazards; bounded attempts, death-loop circuit breaker and no automatic expensive alternative |
| Travel execution | Separate ground/transport states, indoor/mount restrictions, water/air management only when supported, verified transfer outcomes and map-change cancellation |
| Recovery | Distinguish obstruction, no progress, falling, rooted state, loading and disconnect; bounded stop/replan/quarantine escalation, never endless jump spam |
| Persistence | Save configuration and evidence with schema/build revision; re-observe live quest/world state after reload; never resume stale motion, target references, or purchases blindly |
| Supervision | Emergency stop, user-input/movement ownership, chat/UI protection, reasoned pauses, repeated-failure budgets, death/financial circuit breakers and explicit retry |
| Diagnostics | Why a target was rejected, pack evidence/confidence, path provenance, state transitions, event receipts, bounded redacted logs and deterministic replay fixtures |

The playerbots project is useful evidence of separate travel, inventory-error, repair, spirit/corpse and stuck-handling subsystems. It runs inside a server and has privileged access: its quest-state mutation/helper shortcuts and world knowledge are not client-plugin capabilities and must not be copied as assumptions. [S5]

An event-driven state machine with a shared observed-world model and scored candidate actions is a practical starting point. A behavior tree/utility layer can later select goals while safety gates retain final veto. More rules, an LLM, or a large behavior tree alone do not make incomplete evidence reliable. Keep the live movement/safety loop deterministic and bounded.

## 6. Implementation sequence and acceptance gates

1. **Capability and data probe:** read build/API versions, actual map/coordinate conventions, supported events, object visibility and movement behavior; identify local compatible asset inputs. Gate: verified build manifest and reproducible observations.
2. **Owned walking stack:** compiler, mesh versioning, local service, Lua protocol client, follower, cancellation and stop controls. Gate: tested flat ground, stairs, buildings, bridge/cave separation, disconnected endpoints, missing tiles, blocked segments and stale callbacks. No Sentinel runtime loaded.
3. **Threat and pack model:** directed evidence, patrol observations, uncertainty, single-enemy budget, route-aware target scoring and rotation ownership. Gate: known two-/three-member packs are refused, isolated alternatives selected, approaching patrols stop the approach, and ambiguous visibility never claims certainty.
4. **Reliable quest transactions:** reconcile guide/quest states and exact identities with retry budgets. Gate: missed/duplicate/delayed events, stale quest panels, manual progress, loot failures and reloads cannot fake completion or trigger unrelated actions.
5. **Operational recovery:** bags/services, readiness, corpse handling and supported travel links. Gate: full bags, insufficient money, death loops, stalled movement and disconnect all end in bounded, explainable outcomes.
6. **Supervised zone rollout:** one verified level/zone/quest profile first, then expand coverage. Track attempted/aborted pulls, unexpected additional attackers, stuck episodes, unsupported objectives and deaths per attempt, with reasons. Mock tests alone cannot certify in-game safety.

Missing prerequisites are the exact Forever build/asset compatibility, lawful access to compatible local geometry, a verified coordinate transform, and a rotation cooperation interface. Authoritative Forever pack data would improve certainty but is not assumed available. Without it, conservative inference and explicit unknowns are necessary. Zero unexpected adds or universally unattended quest completion cannot honestly be promised.

## Primary-source research ledger

Sources were retrieved directly after the search tool returned no usable results. Pinned revisions make findings reproducible; none proves Forever uses that upstream version.

- **S1 — Recast Navigation**, revision `9f4ce64458dfae86e1239c525ddc219c4e9e06f1`: [modules and geometry-to-mesh pipeline](https://github.com/recastnavigation/recastnavigation/blob/9f4ce64458dfae86e1239c525ddc219c4e9e06f1/README.md), [query/filter/cost and corridor APIs](https://github.com/recastnavigation/recastnavigation/blob/9f4ce64458dfae86e1239c525ddc219c4e9e06f1/Detour/Include/DetourNavMeshQuery.h). Supports the proposed library boundary, not a finished WoW asset decoder or threat planner.
- **S2 — AzerothCore formation documentation**, revision `13cca040e2ad2555f05842b9db3a04c98a23862d`: [groupAI flags and spawn membership](https://github.com/azerothcore/wiki/blob/13cca040e2ad2555f05842b9db3a04c98a23862d/docs/creature_formations.md). Directional assistance is distinct from simply following a leader.
- **S3 — TrinityCore 3.3.5**, revision `bc47ca8fe5b4a8f20272bbc0847217653cc95dc9`: [CreatureGroup::MemberEngagingTarget](https://github.com/TrinityCore/TrinityCore/blob/bc47ca8fe5b4a8f20272bbc0847217653cc95dc9/src/server/game/Entities/Creature/CreatureGroups.cpp), [Creature::CallAssistance / CallForHelp / CanAssistTo](https://github.com/TrinityCore/TrinityCore/blob/bc47ca8fe5b4a8f20272bbc0847217653cc95dc9/src/server/game/Entities/Creature/Creature.cpp). Ordinary assistance also has configuration and state conditions.
- **S4 — CMaNGOS Classic**, revision `d3e2171797614bbed69344fb659a7b59f72f8850`: [CreatureLinkingFlags](https://github.com/cmangos/mangos-classic/blob/d3e2171797614bbed69344fb659a7b59f72f8850/src/game/Entities/CreatureLinkingMgr.h). Explicit aggro linkage can have direction and non-aggro lifecycle effects.
- **S5 — mod-playerbots**, revision `405f81196161017eebae3ee7a612d786a3ed2a9d`: [travel selection](https://github.com/mod-playerbots/mod-playerbots/blob/405f81196161017eebae3ee7a612d786a3ed2a9d/src/Ai/Base/Actions/ChooseTravelTargetAction.cpp), [inventory failures](https://github.com/mod-playerbots/mod-playerbots/blob/405f81196161017eebae3ee7a612d786a3ed2a9d/src/Ai/Base/Actions/InventoryChangeFailureAction.cpp), [action directory](https://github.com/mod-playerbots/mod-playerbots/tree/405f81196161017eebae3ee7a612d786a3ed2a9d/src/Ai/Base/Actions). Research the operational separation, not server-privileged shortcuts.
- **S6 — Polestar**, revision `19bd636e7d6f33231770f6029c76d2d4dfc63798`: [guide engine](https://github.com/fitzforge/polestar/blob/19bd636e7d6f33231770f6029c76d2d4dfc63798/addon/Polestar/Core/Engine.lua), [separate navigation API](https://github.com/fitzforge/polestar/blob/19bd636e7d6f33231770f6029c76d2d4dfc63798/addon/Polestar_Nav/API.lua). Consume guide goals through the supplied host bridge; do not silently adopt its router.
- **Local contract:** `.api/core.lua` for `get_game_build`, map IDs, asynchronous HTTP, raw input, collision/height checks, quests and Polestar; `.api/game_object.lua` for identity/threat and the Forever GUID warning; `.api/common/utility/simple_movement.lua` for the distinction between a movement follower and a world pathfinder. These are documentation stubs, not proof of runtime support on every build.
