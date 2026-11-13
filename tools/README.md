# Local extraction and navigation tools

These tools establish a reproducible local-asset pipeline and an owned loopback service. Diagnostic meshes must not move a character. The Lua runtime no longer uses Sentinel; live-route serving remains blocked until coverage is validated. See [testing guide](../TESTING.md).

## Build

Requires Windows, Git, Visual Studio 2022 C++ tools, and its CMake component. From the repository root:

```powershell
.\tools\Build-Native.ps1 -FetchDependencies
```

The explicit fetch option allows downloading source during setup. Without it, an existing pinned dependency checkout is required. The source revision is `3da55c1d62db78bb89c98e870645fff17b3ec80c`; the script checks the revision and refuses modified CascLib, zlib, or Recast/Detour dependencies. The owned probe links CascLib/zlib, and the diagnostic compiler links Recast/Detour: no Sentinel code, upstream HTTP/key-download wrapper, game server, or navigation endpoint. Build products and downloaded source are ignored under `.local`. Retain the dependencies' license notices when redistributing binaries.

## Probe the actual installation

```powershell
.\tools\Probe-Client.ps1
```

The default bounded sample is the Azeroth WDT, tile `(32,48)`. The default name-to-file-ID mapping (775971) was checked against the public verified listfile release `202611110023`, whose downloaded SHA-256 is `3f12a5c52e81d01fd1a1c97baf0387d8109e27633e953c6af93630cb9825d0f2`. The listfile is development-time name metadata, not a navigation service; the probe does not download or consult it at runtime. A custom WDT name requires successful local name lookup or an explicit `-WdtFileDataId`. A filename mapping does not establish a fully verified physical-map/profile association.

The probe opens local storage explicitly with online mode disabled, verifies `wowf` build 70888, requires declared-present modern WDT tiles, performs strict complete reads, and rejects unknown encrypted data. It exports the selected root ADT and any declared object-placement ADTs, checking version 18. These are collision-phase inputs, not decoded building/model collision. It refuses existing output directories and output inside the game installation. Each wrapper run retains a fresh timestamped receipt with exit status, tool hash, source hashes, optional asset-key-list hash, and log. Failed runs remain failed even if some diagnostics are available.

For explicit local public asset-key input:

```powershell
.\tools\Prepare-AssetKeys.ps1
.\tools\Probe-Client.ps1 -KeysFile .local/WoW-tact-keys.txt
```

The preparation command downloads a static setup dependency from wowdev/TACTKeys revision `1f8ba0ff780db15b1f749a4e36b8c4e2d73f44a4` and verifies SHA-256 `c5354516e1bb787c9fc6982c8e82ca7d8a9ce6355484476a6e261714a61707b9`. These are public content-extraction keys, not account credentials. The probe itself only reads the explicitly supplied local file; it does not fetch keys or missing world data.

`-MetadataOnly` separately tests a strict whole-file Map.db2 read. On the inspected installation this still fails on an unavailable key identifier, while the selected WDT/terrain read succeeds. That does not justify silently accepting encrypted catalog rows as valid.

## Export a terrain-only diagnostic

Use the actual successful receipt path printed by the probe and a new output directory:

```powershell
python tools/Export-Terrain.py .local/client-probes/<run>/receipt.json .local/terrain-samples/<new-sample>
```

The exporter validates the receipt and source digest, ADT version and chunk bounds, all 256 unique grid identities and aligned positions, finite height values, and high-/low-resolution terrain holes. It writes an OBJ plus a manifest under `.local`, refusing overwrites. XYZ is a candidate WoW world coordinate frame, not an in-game-validated transform.

The first real sample produced 37,120 vertices and 65,472 triangles, with 16 terrain-hole cells excluded. **This surface lacks placed building/doodad collision and liquid handling.** It is not a safe walkable mesh. Manifests deliberately retain `navigation_ready: false`; no game movement is initiated.

## Build a diagnostic mesh

```powershell
.\.local\native-build\Release\trentnav_mesh_smoke.exe .local/terrain-samples/<sample>/terrain.obj .local/terrain-samples/<sample>/terrain.tnavdiag
```

Our compiler bounds input size, vertices/triangles and raster dimensions, then runs slope marking, rasterization, clearance/ledge filtering, radius erosion, region/contour generation and Detour serialization. The sample settings (not a verified character profile) are 0.5-unit cells, 0.2-unit height cells, 45-degree slope, 2-unit height, 0.5-unit radius and 0.6-unit climb. World `(x,y,z)` is transformed to Recast `(x,z,-y)` for this diagnostic.

The actual sample produced 2,162 polygons and a complete 18-polygon query between two connected internal polygon centers. The query rejects partial, buffer-truncated and node-exhausted results. This proves the compiler/query integration on the terrain sample, not safe traversal by a real character.

Output deliberately uses `.tnavdiag`, not a production mesh format. Its header contains `TNVDIAG1`, schema 1, client build 70888, coverage flags **0**, and payload length. The owned `trentnav_mesh_query` worker can inspect this artifact through the service’s diagnostic-only endpoint. It cannot authorize live movement. Existing output is refused.

## Combined collision diagnostic

The exporter now instances direct placements and 6,430 active embedded WMO doodads, producing 1,026,900 vertices and 1,176,651 triangles. See [COLLISION.md](COLLISION.md) for reference-derived transforms and limitations.

`Assemble-Diagnostic.py` verifies provenance, clips collision to the terrain tile, and uses `Liquid-Exclusions.py` to conservatively exclude liquid footprints at every height. Use a new output directory:

```powershell
python tools/Assemble-Diagnostic.py --receipt .local/client-probes/20261112-171553-8b816a2ff7b445eda1b80a1ca5233059/receipt.json --collision .local/collision-70888-doodads-final/collision.json --output .local/combined-diagnostic-new
.\.local\native-build\Release\trentnav_mesh_smoke.exe .local/combined-diagnostic-new/combined.obj .local/combined-diagnostic-new/combined.tnavdiag
```

The extended OBJ uses `o diagnostic_combined_collision` and custom `b xmin ymin xmax ymax` records for full-height blocked columns. Ordinary OBJ viewers may ignore these records; only our compiler applies them. Columns expand by one raster cell before agent erosion. This intentionally blocks bridges above water; it does not implement swimming or classify safe liquids.

The verified result under `.local/combined-diagnostic-doodads` has 78,153 vertices, 141,524 triangles, 44 liquid columns and 3,514 walkable polygons. Its verification receipt records zero positive-area polygon/liquid-column intersections, a successful diagnostic HTTP route and HTTP 409 for live routing. Surrounding coverage, dynamic states, client transforms and production agent calibration remain unresolved.

## Validation performed

- Native Release build succeeds with warnings treated as errors for the owned C++ target.
- Actual local WDT and root ADT read successfully with no online storage feature.
- Actual terrain parsing and all exported triangle windings pass.
- Diagnostic mesh compilation and a complete Detour path query pass on the actual sample.
- Native probes reject invalid arguments, forbidden/existing output locations and invalid tile indices; the diagnostic compiler rejects missing vertices, oversized grids/height ranges and unsupported object records.
- In-memory malformed-input checks reject truncated data, oversized chunk bodies, nonfinite height, invalid/duplicate grid identities, and misaligned chunk positions.
- Existing sample output is not overwritten; PowerShell scripts pass syntax parsing.

Remaining gates: complete required collision and liquid extraction, coordinate verification in game, production mesh schema/agent profile/tile connections, and live integration of the implemented follower/threat checks. The loopback service, Lua protocol client and follower now exist and are mock-tested. Do not describe this milestone as completed autonomous navigation.
