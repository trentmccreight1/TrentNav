import argparse
from array import array
import hashlib
import importlib.util
import json
import math
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
LOCAL = (WORKSPACE / ".local").resolve()
MAX_SOURCE_VERTICES = 3000000
MAX_SOURCE_TRIANGLES = 3000000
MAX_OUTPUT_VERTICES = 1000000
MAX_OUTPUT_TRIANGLES = 2000000


def read_json(path):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Metadata exceeds bound")
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8-sig")), hashlib.sha256(raw).hexdigest()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def valid_point(point):
    return len(point) == 3 and all(math.isfinite(value) and abs(value) < 100000 for value in point)


def clip_triangle(triangle, bounds):
    if any(max(vertex[axis] for vertex in triangle) < bounds[axis][0]
           or min(vertex[axis] for vertex in triangle) > bounds[axis][1] for axis in range(2)):
        return []
    polygon = list(triangle)
    for axis in range(2):
        for limit, sign in ((bounds[axis][0], 1), (bounds[axis][1], -1)):
            if not polygon:
                return []
            output = []
            previous = polygon[-1]
            previous_inside = sign * (previous[axis] - limit) >= 0
            for current in polygon:
                inside = sign * (current[axis] - limit) >= 0
                if inside != previous_inside:
                    fraction = (limit - previous[axis]) / (current[axis] - previous[axis])
                    intersection = [previous[dimension] + fraction * (current[dimension] - previous[dimension]) for dimension in range(3)]
                    intersection[axis] = limit
                    output.append(tuple(intersection))
                if inside:
                    output.append(current)
                previous, previous_inside = current, inside
            polygon = output
    return [(polygon[0], polygon[index], polygon[index + 1]) for index in range(1, len(polygon) - 1)]


def obj_triangles(path, expected_vertices, expected_triangles):
    if path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("Collision OBJ exceeds bound")
    points = array("d")
    face_count = 0
    objects = 0
    with path.open("r", encoding="ascii") as source:
        while True:
            line = source.readline(1025)
            if not line:
                break
            if len(line) > 1024:
                raise ValueError("OBJ line exceeds bound")
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "o":
                if len(parts) != 2 or not parts[1].startswith("diagnostic_") or objects >= 100000:
                    raise ValueError("Unexpected OBJ object")
                objects += 1
            elif parts[0] == "v":
                coordinates = tuple(float(value) for value in parts[1:])
                if not valid_point(coordinates) or len(points) // 3 >= MAX_SOURCE_VERTICES:
                    raise ValueError("Invalid or excessive OBJ vertices")
                points.extend(coordinates)
            elif parts[0] == "f":
                indices = [int(value) - 1 for value in parts[1:]]
                if len(indices) != 3 or min(indices) < 0 or max(indices) >= len(points) // 3:
                    raise ValueError("OBJ face references unavailable vertices")
                face_count += 1
                if face_count > MAX_SOURCE_TRIANGLES:
                    raise ValueError("Excessive source triangles")
                yield tuple(tuple(points[index * 3:index * 3 + 3]) for index in indices)
            else:
                raise ValueError("Unsupported OBJ record")
    if not objects or len(points) // 3 != expected_vertices or face_count != expected_triangles:
        raise ValueError("OBJ counts do not match collision manifest")


class Writer:
    def __init__(self, stream):
        self.stream = stream
        self.vertices = {}
        self.triangles = 0
        self.degenerate = 0

    def add(self, triangle):
        triangle = tuple(tuple(round(value, 7) for value in vertex) for vertex in triangle)
        first_edge = [triangle[1][axis] - triangle[0][axis] for axis in range(3)]
        second_edge = [triangle[2][axis] - triangle[0][axis] for axis in range(3)]
        cross = (first_edge[1] * second_edge[2] - first_edge[2] * second_edge[1],
                 first_edge[2] * second_edge[0] - first_edge[0] * second_edge[2],
                 first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0])
        if sum(value * value for value in cross) <= 1e-14:
            self.degenerate += 1
            return
        indices = []
        for vertex in triangle:
            if not valid_point(vertex):
                raise ValueError("Invalid clipped output coordinate")
            if vertex not in self.vertices:
                if len(self.vertices) >= MAX_OUTPUT_VERTICES:
                    raise ValueError("Output vertex budget exhausted")
                self.vertices[vertex] = len(self.vertices) + 1
                self.stream.write("v " + " ".join(f"{value:.7f}" for value in vertex) + "\n")
            indices.append(self.vertices[vertex])
        if self.triangles >= MAX_OUTPUT_TRIANGLES:
            raise ValueError("Output triangle budget exhausted")
        self.stream.write("f " + " ".join(str(index) for index in indices) + "\n")
        self.triangles += 1


def assemble(receipt_path, collision_path, output):
    receipt_path, collision_path, output = receipt_path.resolve(), collision_path.resolve(), output.resolve()
    if not output.is_relative_to(LOCAL) or output == LOCAL or output.exists():
        raise ValueError("Output must be a new directory below .local")
    receipt, receipt_hash = read_json(receipt_path)
    collision, collision_hash = read_json(collision_path)
    if receipt.get("status") != "asset_probe_complete" or receipt.get("exit_code") != 0 or receipt.get("offline_storage_required") is not True:
        raise ValueError("A complete offline extraction receipt is required")
    if collision.get("source_receipt_sha256") != receipt_hash or collision.get("status") != "diagnostic_incomplete":
        raise ValueError("Collision export does not match the source receipt")
    for document in (receipt, collision):
        if document.get("client_version") != "1.60.1.70888" or document.get("product") != "wowf" or document.get("navigation_ready") is not False:
            raise ValueError("Wrong build/product or readiness claim")
    if collision.get("dependencies_complete") is not True or not collision.get("assets") or not collision.get("placements"):
        raise ValueError("Collision dependencies are incomplete")
    if not all(asset.get("decoded") is True for asset in collision["assets"]) or not all(placement.get("geometry_exported") is True for placement in collision["placements"]):
        raise ValueError("Required collision decoding or direct placement is missing")
    if (collision.get("tile_x"), collision.get("tile_y")) != (receipt.get("tile_x"), receipt.get("tile_y")):
        raise ValueError("Tile identity mismatch")
    for field, limit in (("vertices", MAX_SOURCE_VERTICES), ("triangles", MAX_SOURCE_TRIANGLES)):
        if type(collision.get(field)) is not int or not 0 < collision[field] <= limit:
            raise ValueError("Invalid source geometry count")
    model = collision_path.parent / "collision.obj"
    if model.stat().st_size > 256 * 1024 * 1024 or model.stat().st_size != collision.get("obj_size_bytes") or digest(model) != collision.get("obj_sha256"):
        raise ValueError("Collision OBJ hash/length mismatch")
    root_adt = receipt_path.parent / "root.adt"
    entries = [entry for entry in receipt.get("files", []) if entry.get("name") == "root.adt"]
    if len(entries) != 1 or root_adt.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Invalid terrain source")
    terrain_data = root_adt.read_bytes()
    if len(terrain_data) != entries[0]["size_bytes"] or hashlib.sha256(terrain_data).hexdigest() != entries[0]["sha256"]:
        raise ValueError("Terrain source hash mismatch")
    specification = importlib.util.spec_from_file_location("terrain_export", WORKSPACE / "tools/Export-Terrain.py")
    terrain = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(terrain)
    terrain_vertices, terrain_triangles, details = terrain.decode_terrain(terrain_data)
    bounds = details["world_bounds"][:2]
    liquid_specification = importlib.util.spec_from_file_location("liquid_exclusions", WORKSPACE / "tools/Liquid-Exclusions.py")
    liquid_module = importlib.util.module_from_spec(liquid_specification)
    liquid_specification.loader.exec_module(liquid_module)
    exclusions = liquid_module.liquid_columns(terrain_data, terrain, receipt_path, receipt, collision, bounds)
    output.mkdir(parents=True)
    combined = output / "combined.obj"
    accepted_collision = 0
    clipped_collision = 0
    with combined.open("x", encoding="ascii", newline="\n") as stream:
        stream.write("o diagnostic_combined_collision\n")
        for exclusion in exclusions:
            stream.write("b " + " ".join(f"{value:.7f}" for value in exclusion["bounds"]) + "\n")
        writer = Writer(stream)
        for indices in terrain_triangles:
            writer.add(tuple(terrain_vertices[index] for index in indices))
        terrain_count = writer.triangles
        for triangle in obj_triangles(model, collision["vertices"], collision["triangles"]):
            clipped = clip_triangle(triangle, bounds)
            if clipped:
                accepted_collision += 1
                if len(clipped) != 1 or clipped[0] != triangle:
                    clipped_collision += 1
                for fragment in clipped:
                    writer.add(fragment)
        if writer.triangles == terrain_count:
            raise ValueError("No collision intersects selected terrain tile")
    result = {
        "schema_version": 1, "status": "diagnostic_incomplete", "build": 70888, "product": "wowf",
        "navigation_ready": False, "collision_complete": False,
        "source_receipt_sha256": receipt_hash, "collision_report_sha256": collision_hash,
        "source_collision_obj_sha256": collision["obj_sha256"], "source_root_adt_sha256": entries[0]["sha256"],
        "tile_x": receipt["tile_x"], "tile_y": receipt["tile_y"], "world_xy_bounds": bounds,
        "vertices": len(writer.vertices), "triangles": writer.triangles,
        "terrain_triangles": terrain_count, "collision_triangles": writer.triangles - terrain_count,
        "source_collision_triangles_intersecting_tile": accepted_collision,
        "source_collision_triangles_clipped": clipped_collision, "degenerate_triangles_discarded": writer.degenerate,
        "obj_sha256": digest(combined), "obj_size_bytes": combined.stat().st_size,
        "terrain_liquid_chunk_present": details["liquid_chunk_present"],
        "liquid_policy": "conservatively forbid full-height columns; no water/lava classification or swimming",
        "liquid_exclusions": exclusions,
        "unresolved": collision.get("excluded", []) + ["terrain liquid surfaces remain unresolved", "tile boundary clipping is diagnostic, not adjacent-tile coverage", "combined mesh and character profile not verified in game"],
        "source_issues": collision.get("issues", []),
    }
    (output / "combined.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description="Combine matching terrain/collision into a bounded diagnostic; never live-ready")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--collision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(assemble(arguments.receipt, arguments.collision, arguments.output), indent=2))


if __name__ == "__main__":
    main()
