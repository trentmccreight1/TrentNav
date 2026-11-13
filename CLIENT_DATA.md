# Forever client data preflight

Initially inspected read-only on November 12, 2026. Subsequently, an owned offline asset probe was built and run successfully against a bounded terrain sample. No game executable, loader, or navigation service was started, and no game installation files were changed. Generated files remain under this workspace `.local` directory.

## Confirmed local identity

- Client directory: `C:\Program Files (x86)\World of Warcraft\_forever_`.
- Executable: `Wow.exe`; both file and product versions report `1.60.1.70888`.
- Installation root: `C:\Program Files (x86)\World of Warcraft`.
- Shared asset directory: `C:\Program Files (x86)\World of Warcraft\Data`, not `_forever_\Data`.
- Parent `.build.info` active record: version `1.60.1.70888`, product `wowf`.
- `_forever_\.flavor.info` also identifies product `wowf`.
- Active build configuration key: `0597e74b01a3f779d849ffe9b3270196`.
- Matching local build configuration reports `WOW-70888patch1.60.1_Retail` and build UID `wowf`.
- Shared storage contains `Data\config`, `Data\data`, `Data\indices`, and `Data\wowf`; the same installation also contains Classic data. Product selection is mandatory.

The installed build is **70888**. A previously researched community Docker project described **70450**; that project is not proof of compatibility with this installation. These are modern client build identifiers, not historical vanilla patch 1.6.

## Extraction candidate, not yet validated

The `forever` branch of `advocaite/TrinityCore`, inspected at revision `3da55c1d62db78bb89c98e870645fff17b3ec80c`, has a map extractor whose source defaults to product `wowf`. Its source notes the product change at build 70790. This makes it a relevant candidate, not a verified working extractor for all assets in build 70888.

Its local extraction path appends `Data` to the input root. Therefore the candidate input is the parent World of Warcraft installation, not `_forever_`. Source inspection shows a local-storage open path and a separately enabled remote mode; any extraction integration must explicitly select local mode (`-c 0`) and product `wowf`, audit remaining network behavior, and verify it in a network-restricted smoke test. Never silently enable remote CASC or download missing world data.

The candidate exposes `SERVERS` and `TOOLS` CMake options, so a tools-only build is a possible route without running a game server. Our smaller owned probe has now built successfully with Visual Studio 2022, directly linking the pinned CascLib and zlib sources. The full upstream extractor suite has not been validated. The upstream high-level CASC wrapper was found to fetch an online asset-key list even for local storage, so our probe does not use that wrapper.

Before accepting extraction output:

1. Pin and review source/dependencies and their licenses; do not execute unknown downloaded binaries.
2. Build tools in a workspace-owned directory, not inside the game installation.
3. Keep extraction output in a separate workspace-owned directory; the game assets remain input only. Audit cache/write behavior before execution.
4. Confirm offline product/build selection against the manifest above and fail on missing or incompatible assets. Directory/index presence does not prove every required asset is installed.
5. Test a small, bounded region before full-world generation. Reject missing collision, encrypted/zero-filled required geometry, corrupt records, skipped required tiles, and partial success.
6. Verify terrain, placed collision, stairs, liquids, bridge/cave separation, coordinate transforms, and a route with known endpoints in game. Successful extraction alone is not safe navigation.
7. Treat generated formats as versioned inputs to our own service. Do not adopt Sentinel runtime calls or somebody else's navigation endpoint.

## Runtime boundary

The implemented navigation service binds only to loopback and accepts non-browser local requests. No external navigation service, external route fallback, or separate WoW game server is required. Source research/downloads during development are distinct from sending runtime navigation requests to another service. A self-hosted remote deployment would need a separate explicit endpoint/security configuration; loopback is the initial target.

The Lua runtime now uses the owned loopback service and no longer depends on Sentinel. Native diagnostic queries work, but the service refuses live routes and the plugin defaults to read-only diagnostics. Independent runtime plumbing is implemented; complete safe navigation is not. See [TESTING.md](TESTING.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Successful bounded asset and terrain sample

See [tools/README.md](tools/README.md) for reproducible commands and limitations. The latest successful client receipt is `.local/client-probes/20261112-171553-8b816a2ff7b445eda1b80a1ca5233059/receipt.json`. It records build 70888, offline storage, WDT file-data ID 775971, tile `(32,48)`, root ADT file-data ID 778027 and placement ADTs 778028/778029. This strict read succeeds without an additional asset-key file. The corresponding owned terrain export has 256 distinct terrain chunks, 37,120 vertices, 65,472 triangles, and 16 preserved terrain-hole cells.

The expanded probe reads 746 collision dependency assets (411 M2, eight WMO roots and 327 WMO groups), totaling 62,227,869 bytes, with no missing local reads. Direct placements and 6,430 active embedded WMO instances produce 1,026,900 vertices and 1,176,651 triangles. Its report is `.local/collision-70888-doodads-final/collision.json`; the receipt hashes and OBJ digest have been verified. See [collision extraction](tools/COLLISION.md) for reference-derived transforms and limits.

This is still incomplete: transforms follow the pinned offline reference but need in-game verification; adjacent/map-wide coverage and dynamic states remain unsupported. Successful dependency reads do not imply collision-complete geometry. Both `collision_complete` and `navigation_ready` remain false.

The separate diagnostic assembler now combines terrain with clipped collision and 44 conservative full-height liquid exclusions. The resulting mesh has 3,514 polygons; the polygon audit found zero positive-area liquid-column intersections. Exclusions deliberately block bridges too. Liquid classification, swimming/wading, cross-tile connections and production agent calibration remain incomplete. See `.local/combined-diagnostic-doodads/verification.json` and [TESTING.md](TESTING.md).

An owned Recast/Detour diagnostic compiler subsequently built a 2,162-polygon terrain mesh and passed a complete 18-polygon internal query. Its `.tnavdiag` output carries explicit zero coverage flags and is not a production navigation artifact. No character movement was enabled. The later owned service exposes these artifacts only through its diagnostic endpoint.

A separate optional full-extractor dependency bootstrap was stopped after the standalone tools proved sufficient for this milestone. Its OpenSSL installation was not completed and is not a requirement of the owned probe or diagnostic compiler.

The strict whole-file Map.db2 probe encountered missing asset-key identifier `0DA4670DB36FB2A9`, including after importing the pinned public asset-key list. The selected world terrain was independently readable by verified file-data ID. Do not zero-fill unknown encrypted content and claim a complete map/quest catalog.

## Primary sources inspected

- Local executable version resources, parent `.build.info`, client `.flavor.info`, and the matching `Data/config/05/97/0597e74b01a3f779d849ffe9b3270196` build configuration.
- [Pinned map extractor: product, input path and remote-mode selection](https://github.com/advocaite/TrinityCore/blob/3da55c1d62db78bb89c98e870645fff17b3ec80c/src/tools/map_extractor/System.cpp).
- [Pinned CASC storage wrapper: local and remote open paths](https://github.com/advocaite/TrinityCore/blob/3da55c1d62db78bb89c98e870645fff17b3ec80c/src/tools/extractor_common/CascHandles.cpp).
- [Pinned tools/server build options](https://github.com/advocaite/TrinityCore/blob/3da55c1d62db78bb89c98e870645fff17b3ec80c/cmake/options.cmake).
