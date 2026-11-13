# Offline diagnostic collision extraction

This is a build **1.60.1.70888 / wowf** local asset diagnostic, not a navigation provider. Neither successful CASC reads nor successful triangle decoding imply collision completeness. Every collision export sets `status: diagnostic_incomplete`, `collision_complete: false`, and `navigation_ready: false`.

## Run

Build the existing `trentnav_client_probe` target with the Visual Studio CMake executable, then run:

```powershell
& tools/Probe-Client.ps1 -TileX 32 -TileY 48
python tools/Export-Collision.py --receipt .local/client-probes/<new-run>/receipt.json --output .local/collision-<new-run>
```

Python 3.11+ is required. No third-party Python packages, network calls, external navigation services, or client modifications are used. Optional `-KeysFile` remains a local-only CASC key import. The native probe refuses online storage and verifies the selected product/build.

The probe writes `collision-assets.json` and numeric `assets/*.bin` filenames. Its wrapper recursively hashes those files, the manifest, and the WDT/ADT inputs into `receipt.json`. Running the executable alone does not create that SHA-256 wrapper receipt. The exporter requires the receipt and checks actual bytes and lengths before using each source; paths escaping its receipt directory are rejected. Export output must be a new directory below this workspace's `.local` directory.

## What is implemented

- Both selected object ADTs: bounded 36-byte MDDF and 64-byte MODF records, file-data-ID flags `0x40` / `0x08`, legacy ADT string-table identities, and duplicate-placement consistency checks.
- Unique direct M2/WMO extraction. WMO roots recursively enqueue first/base-LOD GFID groups and all nonzero MODI M2 dependencies. MODD indices address the original sparse MODI table, never a compacted list; a zero entry required by an active instance is an explicit missing dependency.
- Strict offline CASC data verification; at most 64 MiB per asset, 4,096 dependencies, 1,024 base groups per WMO, and 512 MiB aggregate dependency reads. Missing/unreadable assets, unsupported named WMO doodad dependencies, malformed dependency arrays, and budget exhaustion remain explicit failures or an incomplete manifest/receipt. Extraction never substitutes empty geometry for missing files.
- MD20 or an explicitly bounded MD21 payload containing MD20, versions 264/272/274. Collision counts and offsets come from the pinned `ModelHeader`, not visual mesh buffers or an arbitrary search for magic bytes. Indices must form triangles and reference finite bounded vertices. Empty collision arrays are recorded as such, distinct from a failed asset.
- WMO version 17 roots/groups, nested 68-byte MOGP headers, MOVT, MOVI/MOVX, and MOPY/MPY2 dimensions. Collision uses unique MOBR triangle references with MOBN node/leaf range validation. The material-flag fallback is deliberately disabled: absent BSP collision is reported unsupported, not guessed. Unreachable/antiportal groups are excluded according to flags and root names.
- Instancing of direct MDDF models, direct MODF base groups, and active embedded MODD models; finite transforms, scale validation, and deterministic OBJ output. Path-only WMO placements are explicitly excluded. Geometry is not cropped to the tile, so large placed buildings can extend beyond tile boundaries.
- Embedded selection intersects the union of default MODS set zero and the valid nonzero MODF-selected set with MODR references from non-excluded base groups. Repeated group references and overlapping sets emit each original MODD index once per parent. An out-of-range selected set falls back to default, matching the pinned extractor, and is reported. Missing groups make selection explicitly incomplete rather than implying no doodads exist.
- Per-asset decode results, hashes, per-placement geometry coverage, missing dependency identities, source receipt hash, and output OBJ hash in `collision.json`. A direct WMO's `geometry_exported` still describes only its base groups. Embedded entries carry `parent_unique_id` and `doodad_index`; `doodad_selections` and `embedded_selection_complete` describe their separate coverage. Empty M2 collision is a successful decode, not missing geometry. Neither coverage flag certifies liquids or navigation.

Python bounds each model to 262,144 vertices / 1,048,576 indices, decoded and instanced totals to 3,000,000 vertices / triangles, and verified input bytes to 512 MiB. These geometry budgets are unchanged. Embedded arrays are limited to 100,000 MODD/MODI records, 4,096 sets, 65,536 MODR entries per group, and 100,000 selected instances across placements. Complete instanced geometry is counted before creating the output directory; exceeding the budget reports exact counts rather than increasing limits. An exception while writing can still leave a partial OBJ without `collision.json`; that is unusable. Existing outputs are never overwritten.

## Coordinate derivation

The reference is the locally pinned TrinityCore revision `3da55c1d62db78bb89c98e870645fff17b3ec80c`:

- `src/tools/vmap4_extractor/modelheaders.h`: M2 collision array fields at byte offsets 216 through 236, relative to MD20.
- `model.cpp`: `fixCoordSystem` followed by `ConvertToVMAPModel` cancels the M2 vertex axis conversion; triangle vertices two and three are exchanged. MDDF scale divides by 1024.
- `wmo.h` / `wmo.cpp`: placement position becomes `(pos.z, pos.x, pos.y)`; MODF scale applies only with flag `0x04`. Group vertices remain in their stored model frame.
- `src/common/Collision/Models/ModelInstance.cpp` and `dep/g3dlite/source/Matrix3.cpp`: forward rotation is `Rz(rotation.y) * Ry(rotation.x) * Rx(rotation.z)`, with input degrees converted to radians.
- `src/common/Collision/Management/VMapManager.cpp`: internal/world conversion is `(MID-x, MID-y, z)`, where `MID = 32 * 533.33333333`.

Thus each exported vertex is obtained by scaling, applying that rotation, adding the permuted placement translation, and finally converting internal coordinates to world coordinates. This is reference-derived, not an unverified Euler-order guess. It still needs in-game landmark/raycast verification before being used for movement. Numerical fixture rotations and all eight real MODF world-space bounds have been checked; reference double-versus-client-float rounding is not a claim of exact bitwise agreement.

## Embedded reference details

`vmap4_extractor/vmapexport.cpp` collects MODR only after `ShouldSkip`; references are a set. `model.cpp:Doodad::ExtractSet` emits set zero and then an in-range nonzero selected set. This exporter additionally deduplicates overlapping sets by original MODD index. Out-of-range MODR references are rejected instead of silently skipped. Required model failures are reported instead of inheriting the reference extractor's silent omission of failed models.

For each embedded record, translation is `Pparent + Rparent * MODD.position`. `G3D::Quat(X,Y,Z,W).toRotationMatrix()` normalizes the quaternion (`dep/g3dlite/source/Matrix3.cpp`); zero/near-zero, nonfinite, or excessive components are rejected here. The extractor forms `Q * Rparent`, extracts **XYZ** Euler angles, and stores them as `(rotation.z, rotation.x, rotation.y)`. `ModelInstance.cpp` reconstructs **ZYX** from those stored angles. The exporter reproduces this round-trip, including both gimbal branches; it does **not** substitute conventional `Rparent * Q` composition or directly apply the pre-round-trip matrix. Scale is **MODD scale only**: the pinned `ExtractSet` neither scales the embedded offset nor multiplies its scale by MODF scale. Direct WMO shell scaling remains unchanged.

`src/common/mmaps_common/Generator/TerrainBuilder.cpp:loadVMap/transformVertices` consumes the instance inverse rotation as a row-vector multiplication, equivalent to the forward rotation used here, scales, translates, mirrors horizontal axes, and finally reorders world XYZ into Recast YZX. This OBJ remains **world XYZ**, leaving that final Recast permutation to downstream assembly.

The pinned root loader sizes arrays from chunk lengths, not MOHD counts. The sample's two large roots declare 6,751/171 doodads but contain 6,106/150 MODD records. Header counts are retained as provenance; validated chunk sizes govern actual ranges. A header-count equality requirement would incorrectly discard these real roots.

## Deliberate blockers

1. **Embedded instancing follows the pinned offline reference, not certified client behavior.** Quaternion composition and parent-scale omission match that implementation, including its Euler round-trip. In-game landmark/raycast validation and unsupported named MODN identities remain gates; do not treat this as certified complete interiors.
2. **Liquids are not classified for traversal.** The collision exporter reports MLIQ presence/raw type. The separate `Assemble-Diagnostic.py` and `Liquid-Exclusions.py` tools conservatively exclude occupied ADT chunks and transformed WMO liquid footprints at every height. This blocks bridges too; swimming/wading and accurate liquid semantics remain unsupported.
3. **Only the requested tile's placement inputs are considered.** Neighbor tile dependencies, global-WMO maps, map-wide coverage, cross-tile connectivity, dynamic obstacles/destructible states, and transport handling remain unverified or unsupported.
4. **No production mesh or live route is produced.** A separate bounded assembler/compiler now produces a combined diagnostic mesh with liquid exclusions. Production coverage, tile connectivity, agent calibration and live coordinate validation remain gates. Source assets that all exist are still not collision-complete.

## Observed sample and validation

On Azeroth WDT 775971, tile `(32,48)`, the expanded local probe reads **746 assets / 62,227,869 bytes**: **411 M2, 8 WMO roots, 327 WMO groups**, with no missing local reads. The original placement inputs contain **785 MDDF + 8 MODF = 793** unique direct placements referencing **76 M2 + 8 WMO** assets. Embedded dependencies account for the additional M2 files.

The original direct-placement export has **950,464 vertices and 1,051,736 triangles**. The expanded export has **1,026,900 vertices and 1,176,651 triangles**, adding **76,436 vertices / 124,915 triangles** from **6,430 active embedded instances**. Five roots contain 6,467 MODD records; 37 lack an active retained-group reference. Across retained groups, 6,687 MODR entries deduplicate to 6,430 indices. All eight parent selections complete without missing required models or decode issues. Sixteen groups are excluded as unreachable/antiportal; three contain MLIQ. Observed M2 versions remain 409 files at 272 and two at 274.

The verified source is `.local/client-probes/20261112-171553-8b816a2ff7b445eda1b80a1ca5233059/receipt.json`. The final expanded output is `.local/collision-70888-doodads-final/collision.obj` and its sibling `collision.json`. OBJ SHA-256: `df08aea655b5adcb2886f5e9908649dec93a019d3e41341f925ddd7c13b8786b`; size 68,009,922 bytes. Exporter reads total 749 receipted files / 62,400,031 bytes including the manifest and two ADTs (the 746 asset files alone total 62,227,869 bytes). Earlier `doodads-verified` output is an incomplete development run and must not be used.

Validation uses ephemeral local fixtures, not a permanent test suite: truncated/oversized/duplicate chunks, short MD20 and MD21 payload escape, out-of-range counts/offsets/indices, NaN geometry, invalid BSP references, no-BSP rejection, malformed placements, path traversal, duplicate source names, hash mismatches, explicit missing-required-model receipts, and conflicting duplicate placement IDs. A missing model produces an incomplete diagnostic with no falsely covered placement; conflicting placement IDs fail before output creation. Native out-of-range tile input is rejected before opening storage. The real WMO triangle extents remain inside all eight corresponding MODF bounds.

Embedded validation adds malformed MODS/MODD/MODI lengths and ranges, zero/NaN/infinite/near-zero quaternions, quaternion normalization, invalid scales, sparse-zero required models, MODR range rejection, default/selected/fallback/empty sets, overlap and repeated-reference deduplication, parent-scale omission, gimbal branches, and 100 seeded mixed-axis transforms checked with independent axis-matrix multiplication. Tiny receipted end-to-end fixtures verify missing-required-model reporting and preflight output-budget rejection. No permanent test suite is added. The final real OBJ is checked for finite vertices, valid face indices, exact counts, and digest.
