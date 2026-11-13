import argparse
import hashlib
import json
import math
import pathlib
import struct


def chunks(data, start=0, end=None):
    end = len(data) if end is None else end
    offset = start
    while offset < end:
        if end - offset < 8:
            raise ValueError("Truncated chunk header")
        tag = data[offset:offset + 4][::-1].decode("ascii")
        size = struct.unpack_from("<I", data, offset + 4)[0]
        body = offset + 8
        if size > end - body:
            raise ValueError(f"Truncated {tag} chunk")
        yield tag, body, size
        offset = body + size


def decode_terrain(data):
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("ADT exceeds bounded sample size")
    vertices = []
    triangles = []
    identities = set()
    hole_cells = 0
    liquid_present = False
    version_seen = False
    tile_origin = None
    unit = (1600.0 / 3.0) / 128.0
    for tag, body, size in chunks(data):
        if tag == "MVER":
            if version_seen or size != 4 or struct.unpack_from("<I", data, body)[0] != 18:
                raise ValueError("Unsupported or duplicate ADT version")
            version_seen = True
        if tag == "MH2O":
            liquid_present = True
        if tag != "MCNK":
            continue
        if size < 128:
            raise ValueError("Truncated terrain header")
        flags, chunk_x, chunk_y = struct.unpack_from("<3I", data, body)
        identity = (chunk_x, chunk_y)
        if chunk_x >= 16 or chunk_y >= 16 or identity in identities:
            raise ValueError("Invalid or duplicate terrain chunk identity")
        identities.add(identity)
        base_x, base_y, base_z = struct.unpack_from("<3f", data, body + 104)
        if not all(math.isfinite(value) and abs(value) < 100000 for value in (base_x, base_y, base_z)):
            raise ValueError("Invalid terrain position")
        origin = (base_x + chunk_y * unit * 8, base_y + chunk_x * unit * 8)
        if tile_origin is None:
            tile_origin = origin
        if max(abs(origin[axis] - tile_origin[axis]) for axis in range(2)) > 0.02:
            raise ValueError("Terrain positions disagree with chunk grid identities")
        heights = None
        for subtag, subbody, subsize in chunks(data, body + 128, body + size):
            if subtag == "MCLQ":
                liquid_present = True
            if subtag == "MCVT":
                if heights is not None or subsize != 145 * 4:
                    raise ValueError("Invalid or duplicate terrain height array")
                heights = struct.unpack_from("<145f", data, subbody)
        if heights is None or not all(math.isfinite(value) and abs(value) < 100000 for value in heights):
            raise ValueError("Missing or invalid required height values")
        high_holes = data[body + 20:body + 28]
        low_holes = struct.unpack_from("<I", data, body + 60)[0] & 0xFFFF
        base_index = len(vertices)
        for row in range(9):
            for column in range(9):
                vertices.append((base_x - row * unit, base_y - column * unit, base_z + heights[row * 17 + column]))
            if row < 8:
                for column in range(8):
                    vertices.append((base_x - (row + 0.5) * unit, base_y - (column + 0.5) * unit, base_z + heights[row * 17 + 9 + column]))
        for row in range(8):
            for column in range(8):
                hole = bool(high_holes[row] & (1 << column)) if flags & 0x10000 else bool(low_holes & (1 << ((row // 2) * 4 + column // 2)))
                if hole:
                    hole_cells += 1
                    continue
                top_left = base_index + row * 17 + column
                top_right = top_left + 1
                bottom_left = top_left + 17
                bottom_right = bottom_left + 1
                center = base_index + row * 17 + 9 + column
                triangles.extend(((top_left, center, top_right), (top_right, center, bottom_right), (bottom_right, center, bottom_left), (bottom_left, center, top_left)))
    if not version_seen or len(identities) != 256 or not triangles:
        raise ValueError("Incomplete terrain tile: require version 18 and 256 distinct terrain chunks")
    bounds = [[min(vertex[axis] for vertex in vertices), max(vertex[axis] for vertex in vertices)] for axis in range(3)]
    if bounds[0][1] - bounds[0][0] > 534 or bounds[1][1] - bounds[1][0] > 534:
        raise ValueError("Terrain chunk positions do not form a single bounded tile")
    return vertices, triangles, {"terrain_chunks": len(identities), "hole_cells": hole_cells, "liquid_chunk_present": liquid_present, "world_bounds": bounds}


def main():
    parser = argparse.ArgumentParser(description="Export a strictly validated, terrain-only diagnostic sample; never navigation-ready")
    parser.add_argument("receipt", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    arguments = parser.parse_args()
    workspace = pathlib.Path(__file__).resolve().parent.parent
    output = arguments.output.resolve()
    if not output.is_relative_to((workspace / ".local").resolve()):
        raise ValueError("Generated terrain must stay inside this workspace .local directory")
    if output.exists():
        raise ValueError("Refusing to overwrite an existing terrain sample")
    receipt_path = arguments.receipt.resolve()
    if receipt_path.stat().st_size > 256 * 1024:
        raise ValueError("Receipt exceeds bounded metadata size")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if receipt.get("status") != "asset_probe_complete" or receipt.get("exit_code") != 0 or receipt.get("client_version") != "1.60.1.70888" or receipt.get("product") != "wowf":
        raise ValueError("A successful matching client asset receipt is required")
    root = receipt_path.parent / "root.adt"
    with root.open("rb") as source:
        data = source.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("Root ADT exceeds bounded sample size")
    digest = hashlib.sha256(data).hexdigest()
    expected = [entry for entry in receipt["files"] if entry["name"] == "root.adt"]
    if len(expected) != 1 or expected[0]["sha256"] != digest or expected[0]["size_bytes"] != len(data):
        raise ValueError("Root ADT does not match its extraction receipt")
    vertices, triangles, details = decode_terrain(data)
    output.mkdir(parents=True)
    with (output / "terrain.obj").open("x", encoding="ascii", newline="\n") as model:
        model.write("o diagnostic_terrain_only\n")
        for world_x, world_y, height in vertices:
            model.write(f"v {world_x:.7f} {world_y:.7f} {height:.7f}\n")
        for first, second, third in triangles:
            model.write(f"f {first + 1} {second + 1} {third + 1}\n")
    manifest = {
        "schema_version": 1,
        "client_version": receipt["client_version"],
        "product": receipt["product"],
        "source_adt_sha256": digest,
        "tile_x": receipt["tile_x"],
        "tile_y": receipt["tile_y"],
        "vertices": len(vertices),
        "triangles": len(triangles),
        "coordinate_frame": "candidate_wow_world_xyz_not_yet_verified_in_game",
        "navigation_ready": False,
        "excluded": ["placed_building_and_doodad_collision", "liquid_navigation", "dynamic_obstacles", "cross_tile_connections"],
        **details,
    }
    (output / "terrain.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
