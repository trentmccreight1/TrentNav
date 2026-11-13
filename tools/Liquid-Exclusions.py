import importlib.util
import itertools
import math
from pathlib import Path
import struct

UNIT = (1600.0 / 3.0) / 128.0


def liquid_columns(terrain_data, terrain, receipt_path, receipt, collision, bounds):
    specification = importlib.util.spec_from_file_location("collision_export", Path(__file__).with_name("Export-Collision.py"))
    exporter = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(exporter)
    sources = exporter.Sources(receipt_path.parent, receipt)
    columns = []

    def add(minimum_x, minimum_y, maximum_x, maximum_y, reason):
        coordinates = (minimum_x, minimum_y, maximum_x, maximum_y)
        if not all(math.isfinite(value) and abs(value) < 100000 for value in coordinates):
            raise ValueError("Invalid liquid exclusion bounds")
        if minimum_x > maximum_x or minimum_y > maximum_y:
            raise ValueError("Inverted liquid exclusion bounds")
        clipped = (max(minimum_x, bounds[0][0]), max(minimum_y, bounds[1][0]),
                   min(maximum_x, bounds[0][1]), min(maximum_y, bounds[1][1]))
        if clipped[0] < clipped[2] and clipped[1] < clipped[3]:
            if len(columns) >= 4096:
                raise ValueError("Liquid exclusion budget exceeded")
            columns.append({"bounds": clipped, "reason": reason})

    chunks = list(terrain.chunks(terrain_data))
    positions = {}
    for tag, body, size in chunks:
        if tag != "MCNK":
            continue
        chunk_x, chunk_y = struct.unpack_from("<2I", terrain_data, body + 4)
        world_x, world_y = struct.unpack_from("<2f", terrain_data, body + 104)
        positions[chunk_y * 16 + chunk_x] = (world_x, world_y)
        for subtag, subbody, subsize in terrain.chunks(terrain_data, body + 128, body + size):
            if subtag == "MCLQ":
                add(world_x - 8 * UNIT, world_y - 8 * UNIT, world_x, world_y, "legacy_MCLQ_chunk")
    liquid_chunks = [(body, size) for tag, body, size in chunks if tag == "MH2O"]
    if len(liquid_chunks) > 1:
        raise ValueError("Duplicate MH2O")
    for body, size in liquid_chunks:
        if size < 256 * 12:
            raise ValueError("Short MH2O headers")
        for index in range(256):
            offset, layers, attributes = struct.unpack_from("<3I", terrain_data, body + index * 12)
            if layers == 0:
                continue
            if layers > 64 or offset < 256 * 12 or offset + layers * 24 > size:
                raise ValueError("Invalid MH2O instance range")
            if attributes and (attributes < 256 * 12 or attributes + 16 > size):
                raise ValueError("Invalid MH2O attribute range")
            world_x, world_y = positions[index]
            add(world_x - 8 * UNIT, world_y - 8 * UNIT, world_x, world_y, "MH2O_occupied_chunk")

    assets = {(asset["kind"], asset["file_data_id"], asset["name"]): asset for asset in collision["assets"]}
    for placement in collision["placements"]:
        if placement["kind"] != "wmo":
            continue
        root = assets[("wmo", placement["file_data_id"], placement["name"])]
        root_header = exporter.chunk_map(sources.read(root["path"])).get("MOHD", b"")
        if len(root_header) < 64:
            raise ValueError("Short WMO root header for liquid policy")
        root_flags = struct.unpack_from("<H", root_header, 60)[0]
        for group_id in root["groups"]:
            group = assets[("wmo_group", group_id, "")]
            raw_type = group.get("liquid_type_raw")
            if type(raw_type) is not int:
                raise ValueError("Missing WMO liquid type observation")
            implicit_liquid = raw_type != 0 if root_flags & 4 else raw_type != 15
            if not group.get("liquid_present") and not implicit_liquid:
                continue
            parts = exporter.chunk_map(sources.read(group["path"]))
            group_body = parts.get("MOGP", b"")
            if len(group_body) < 68:
                raise ValueError("Invalid liquid WMO group")
            liquid = exporter.chunk_map(group_body[68:]).get("MLIQ", b"")
            if not liquid and implicit_liquid:
                local_bounds = struct.unpack_from("<6f", group_body, 12)
                exporter.finite(local_bounds)
                if any(local_bounds[axis] > local_bounds[axis + 3] for axis in range(3)):
                    raise ValueError("Invalid implicit-liquid group bounds")
                world = [exporter.transform(point, placement) for point in itertools.product(
                    *((local_bounds[axis], local_bounds[axis + 3]) for axis in range(3)))]
                add(min(point[0] for point in world), min(point[1] for point in world),
                    max(point[0] for point in world), max(point[1] for point in world), "WMO_implicit_liquid_group")
                continue
            if len(liquid) < 30:
                raise ValueError("Short WMO MLIQ header")
            vertices_x, vertices_y, tiles_x, tiles_y, position_x, position_y, position_z, material = struct.unpack_from("<4I3fH", liquid)
            if not 1 <= tiles_x <= 1024 or not 1 <= tiles_y <= 1024 or vertices_x != tiles_x + 1 or vertices_y != tiles_y + 1:
                raise ValueError("Unsupported liquid grid dimensions")
            vertex_count = vertices_x * vertices_y
            if vertex_count > 262144 or len(liquid) != 30 + vertex_count * 8 + tiles_x * tiles_y:
                raise ValueError("Invalid liquid grid byte length")
            exporter.finite((position_x, position_y, position_z))
            heights = [struct.unpack_from("<f", liquid, 30 + index * 8 + 4)[0] for index in range(vertex_count)]
            exporter.finite(heights)
            world = [exporter.transform(point, placement) for point in itertools.product(
                (position_x, position_x + tiles_x * UNIT), (position_y, position_y + tiles_y * UNIT),
                (min(heights), max(heights)))]
            add(min(point[0] for point in world), min(point[1] for point in world),
                max(point[0] for point in world), max(point[1] for point in world), "WMO_MLIQ_full_grid")
    return columns
