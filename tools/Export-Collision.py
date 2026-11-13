import argparse
import hashlib
import json
import math
from pathlib import Path
import struct


MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_VERTICES = 262144
MAX_INDICES = 1048576
MAX_OUTPUT = 3000000
MID = 32 * 533.33333333


def chunks(data, start=0, end=None):
    end = len(data) if end is None else end
    if start < 0 or end > len(data) or start > end:
        raise ValueError("Invalid chunk range")
    while start < end:
        if end - start < 8:
            raise ValueError("Truncated chunk header")
        raw, size = struct.unpack_from("<4sI", data, start)
        if size > end - start - 8:
            raise ValueError("Truncated chunk body")
        tag = raw.decode("ascii")
        if tag != "MD21":
            tag = tag[::-1]
        yield tag, memoryview(data)[start + 8:start + 8 + size]
        start += 8 + size


def chunk_map(data, wanted=None):
    result = {}
    for tag, body in chunks(data):
        if wanted is None or tag in wanted:
            if tag in result:
                raise ValueError(f"Duplicate {tag} chunk")
            result[tag] = body
    return result


def integer(data, offset=0):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("Truncated integer")
    return struct.unpack_from("<I", data, offset)[0]


def array(data, fmt, maximum):
    stride = struct.calcsize(fmt)
    if len(data) % stride or len(data) // stride > maximum:
        raise ValueError("Invalid or excessive array size")
    return list(struct.iter_unpack(fmt, data))


def finite(values, bound=1000000):
    if not all(math.isfinite(value) and abs(value) <= bound for value in values):
        raise ValueError("Nonfinite or out-of-range geometry")


def vertices(data):
    points = array(data, "<3f", MAX_VERTICES)
    for point in points:
        finite(point)
    return points


def checked_triangles(values, point_count):
    if len(values) % 3 or len(values) > MAX_INDICES:
        raise ValueError("Invalid triangle index count")
    if any(index >= point_count for index in values):
        raise ValueError("Triangle vertex index out of bounds")
    return [tuple(values[offset:offset + 3]) for offset in range(0, len(values), 3)]


def decode_m2(data):
    if data[:4] == b"MD20":
        body = memoryview(data)
    else:
        parts = chunk_map(data)
        body = parts.get("MD21")
    if body is None or len(body) < 240 or bytes(body[:4]) != b"MD20":
        raise ValueError("Expected MD20 or MD21 containing MD20")
    version = integer(body, 4)
    if version not in (264, 272, 274):
        raise ValueError(f"Unsupported M2 version {version}")
    index_count, index_offset, point_count, point_offset, normal_count, normal_offset = struct.unpack_from("<6I", body, 216)
    for count, offset, stride, maximum in ((index_count, index_offset, 2, MAX_INDICES), (point_count, point_offset, 12, MAX_VERTICES), (normal_count, normal_offset, 12, MAX_INDICES // 3)):
        if count > maximum or offset > len(body) or count * stride > len(body) - offset or (count and offset < 240):
            raise ValueError("M2 collision array outside bounded MD20 payload")
    points = vertices(body[point_offset:point_offset + point_count * 12])
    indices = [value[0] for value in array(body[index_offset:index_offset + index_count * 2], "<H", MAX_INDICES)]
    faces = checked_triangles(indices, len(points))
    for normal in array(body[normal_offset:normal_offset + normal_count * 12], "<3f", MAX_INDICES // 3):
        finite(normal)
    faces = [(first, third, second) for first, second, third in faces]
    return points, faces, {"format": "m2", "version": version, "empty_collision": not faces}


def decode_group(data, group_names=None):
    top = chunk_map(data)
    if integer(top.get("MVER", b"")) != 17:
        raise ValueError("Unsupported WMO group version")
    body = top.get("MOGP", b"")
    if len(body) < 68:
        raise ValueError("Short WMO group header")
    flags = integer(body, 8)
    parts = chunk_map(body[68:])
    references_to_doodads = array(parts.get("MODR", b""), "<H", 65536)
    details = {"format": "wmo_group", "flags": flags, "liquid_present": "MLIQ" in parts, "liquid_type_raw": integer(body, 52), "doodad_references": len(references_to_doodads), "doodad_indices": [reference[0] for reference in references_to_doodads]}
    name_offset = integer(body)
    antiportal = group_names is not None and name_offset < len(group_names) and string_at(group_names, name_offset) == "antiportal"
    if flags & (0x80 | 0x4000000) or antiportal:
        return [], [], {**details, "excluded": "unreachable_or_antiportal"}
    points = vertices(parts.get("MOVT", b""))
    index_tag = "MOVX" if "MOVX" in parts else "MOVI"
    index_fmt = "<I" if index_tag == "MOVX" else "<H"
    if index_tag not in parts:
        raise ValueError("Missing WMO triangle indices")
    indices = [value[0] for value in array(parts[index_tag], index_fmt, MAX_INDICES)]
    faces = checked_triangles(indices, len(points))
    materials = parts.get("MPY2", parts.get("MOPY", b""))
    material_stride = 4 if "MPY2" in parts else 2
    if len(materials) != len(faces) * material_stride:
        raise ValueError("WMO material/triangle count mismatch")
    nodes = array(parts.get("MOBN", b""), "<HhhHIf", 65536)
    references = [value[0] for value in array(parts.get("MOBR", b""), "<H", MAX_INDICES)]
    if not nodes or not references:
        raise ValueError("No authoritative MOBN/MOBR collision; material heuristic intentionally disabled")
    if any(reference >= len(faces) for reference in references):
        raise ValueError("BSP triangle reference out of bounds")
    for node_flags, negative, positive, count, first, distance in nodes:
        finite((distance,))
        if negative < -1 or positive < -1 or negative >= len(nodes) or positive >= len(nodes):
            raise ValueError("BSP child index out of bounds")
        if first > len(references) or count > len(references) - first:
            raise ValueError("BSP leaf reference range out of bounds")
    selected = sorted(set(references))
    return points, [faces[index] for index in selected], {**details, "bsp_triangles": len(selected), "render_triangles": len(faces)}


def string_at(data, offset):
    if offset >= len(data):
        raise ValueError("String offset out of bounds")
    tail = bytes(data[offset:offset + 1025])
    end = tail.find(b"\0")
    if end <= 0 or end > 1024:
        raise ValueError("Unterminated or excessive name")
    return tail[:end].decode("ascii")


def placements(data, source):
    parts = chunk_map(data, {"MVER", "MDDF", "MODF", "MMID", "MMDX", "MWID", "MWMO"})
    if integer(parts.get("MVER", b"")) != 18:
        raise ValueError("Unsupported object ADT version")
    result = []
    for tag, stride in (("MDDF", 36), ("MODF", 64)):
        records = parts.get(tag, b"")
        if len(records) % stride or len(records) // stride > 100000:
            raise ValueError("Invalid placement record count")
        for offset in range(0, len(records), stride):
            record = records[offset:offset + stride]
            identity, unique_id = struct.unpack_from("<2I", record)
            position = struct.unpack_from("<3f", record, 8)
            rotation = struct.unpack_from("<3f", record, 20)
            finite(position)
            finite(rotation, 36000)
            is_m2 = tag == "MDDF"
            flags = struct.unpack_from("<H", record, 34 if is_m2 else 56)[0]
            scale_raw = struct.unpack_from("<H", record, 32 if is_m2 else 62)[0]
            scale = scale_raw / 1024 if is_m2 or flags & 4 else 1
            if scale <= 0 or scale > 64:
                raise ValueError("Invalid placement scale")
            direct = flags & (0x40 if is_m2 else 8)
            name = ""
            if not direct:
                offsets = parts.get("MMID" if is_m2 else "MWID", b"")
                if len(offsets) % 4 or identity >= len(offsets) // 4:
                    raise ValueError("Placement name index out of bounds")
                name = string_at(parts.get("MMDX" if is_m2 else "MWMO", b""), integer(offsets, identity * 4))
                if is_m2 and name[-4:].lower() in (".mdx", ".mdl"):
                    name = name[:-4] + ".m2"
            result.append({"kind": "m2" if is_m2 else "wmo", "file_data_id": identity if direct else 0, "name": name, "unique_id": unique_id, "position": position, "rotation": rotation, "scale": scale, "flags": flags, "source": source, "doodad_set": None if is_m2 else struct.unpack_from("<H", record, 58)[0]})
    return result


def transform(point, placement):
    rotation_x, rotation_y, rotation_z = (math.radians(angle) for angle in placement["rotation"])
    point_x, point_y, point_z = (value * placement["scale"] for value in point)
    point_y, point_z = math.cos(rotation_z) * point_y - math.sin(rotation_z) * point_z, math.sin(rotation_z) * point_y + math.cos(rotation_z) * point_z
    point_x, point_z = math.cos(rotation_x) * point_x + math.sin(rotation_x) * point_z, -math.sin(rotation_x) * point_x + math.cos(rotation_x) * point_z
    point_x, point_y = math.cos(rotation_y) * point_x - math.sin(rotation_y) * point_y, math.sin(rotation_y) * point_x + math.cos(rotation_y) * point_y
    position_x, position_y, position_z = placement["position"]
    result = (MID - position_z - point_x, MID - position_x - point_y, position_y + point_z)
    finite(result)
    return result


def quaternion_matrix(values):
    finite(values)
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude <= 1e-12:
        raise ValueError("Degenerate WMO doodad quaternion")
    quat_x, quat_y, quat_z, quat_w = (value / magnitude for value in values)
    return (
        (1 - 2 * (quat_y * quat_y + quat_z * quat_z), 2 * (quat_x * quat_y - quat_z * quat_w), 2 * (quat_x * quat_z + quat_y * quat_w)),
        (2 * (quat_x * quat_y + quat_z * quat_w), 1 - 2 * (quat_x * quat_x + quat_z * quat_z), 2 * (quat_y * quat_z - quat_x * quat_w)),
        (2 * (quat_x * quat_z - quat_y * quat_w), 2 * (quat_y * quat_z + quat_x * quat_w), 1 - 2 * (quat_x * quat_x + quat_y * quat_y)),
    )


def decode_doodads(parts):
    doodads = array(parts.get("MODD", b""), "<I3f4ffI", 100000)
    sets = array(parts.get("MODS", b""), "<20sIII", 4096)
    model_ids = [item[0] for item in array(parts.get("MODI", b""), "<I", 100000)]
    if doodads and ("MODN" in parts or "MODI" not in parts):
        raise ValueError("Unsupported named WMO doodad identities")
    for doodad in doodads:
        if (doodad[0] & 0xFFFFFF) >= len(model_ids):
            raise ValueError("WMO doodad model index outside sparse MODI table")
        finite(doodad[1:4])
        quaternion_matrix(doodad[4:8])
        if not math.isfinite(doodad[8]) or not 0 < doodad[8] <= 64:
            raise ValueError("Invalid WMO doodad scale")
    for set_name, start, length, padding in sets:
        if start > len(doodads) or length > len(doodads) - start:
            raise ValueError("WMO doodad set outside record array")
    return {"records": doodads, "sets": sets, "model_ids": model_ids, "header_records": integer(parts["MOHD"], 20), "header_sets": integer(parts["MOHD"], 24)}


def active_doodads(root, references, selected):
    if any(reference >= len(root["records"]) for reference in references):
        raise ValueError("WMO MODR reference outside MODD array")
    set_indices = [0] if root["sets"] else []
    if 0 < selected < len(root["sets"]):
        set_indices.append(selected)
    active = set()
    for set_index in set_indices:
        _, start, count, _ = root["sets"][set_index]
        active.update(reference for reference in references if start <= reference < start + count)
    return sorted(active), set_indices


def doodad_placement(parent, doodad, index, model_id):
    rotation_x, rotation_y, rotation_z = (math.radians(angle) for angle in parent["rotation"])
    cos_x, sin_x = math.cos(rotation_x), math.sin(rotation_x)
    cos_y, sin_y = math.cos(rotation_y), math.sin(rotation_y)
    cos_z, sin_z = math.cos(rotation_z), math.sin(rotation_z)
    parent_matrix = (
        (cos_y * cos_x, cos_y * sin_x * sin_z - sin_y * cos_z, cos_y * sin_x * cos_z + sin_y * sin_z),
        (sin_y * cos_x, sin_y * sin_x * sin_z + cos_y * cos_z, sin_y * sin_x * cos_z - cos_y * sin_z),
        (-sin_x, cos_x * sin_z, cos_x * cos_z),
    )
    quaternion = quaternion_matrix(doodad[4:8])
    combined = tuple(tuple(sum(quaternion[row][axis] * parent_matrix[axis][column] for axis in range(3)) for column in range(3)) for row in range(3))
    if -1 < combined[0][2] < 1:
        angle_x = math.atan2(-combined[1][2], combined[2][2])
        angle_y = math.asin(combined[0][2])
        angle_z = math.atan2(-combined[0][1], combined[0][0])
    else:
        sign = 1 if combined[0][2] >= 1 else -1
        angle_x = sign * math.atan2(combined[1][0], combined[1][1])
        angle_y = sign * math.pi / 2
        angle_z = 0
    offset = tuple(sum(row[axis] * doodad[axis + 1] for axis in range(3)) for row in parent_matrix)
    position_x, position_y, position_z = parent["position"]
    return {"kind": "m2", "file_data_id": model_id, "name": "", "unique_id": f"{parent['unique_id']}_doodad_{index}", "parent_unique_id": parent["unique_id"], "parent_file_data_id": parent["file_data_id"], "doodad_index": index, "position": (position_x + offset[1], position_y + offset[2], position_z + offset[0]), "rotation": tuple(math.degrees(angle) for angle in (angle_y, angle_z, angle_x)), "scale": doodad[8], "flags": doodad[0] >> 24, "source": parent["source"], "doodad_set": parent["doodad_set"]}


class Sources:
    def __init__(self, root, receipt):
        self.root = root.resolve()
        self.expected = {}
        self.cache = {}
        self.total = 0
        entries = receipt.get("files", [])
        if not isinstance(entries, list) or len(entries) > 4110:
            raise ValueError("Invalid receipt file count")
        for entry in entries:
            name = entry["name"]
            if name in self.expected:
                raise ValueError("Duplicate receipt file")
            path = (self.root / name).resolve()
            if not path.is_relative_to(self.root) or path == self.root:
                raise ValueError("Receipt path escapes source directory")
            self.expected[name] = entry

    def read(self, name):
        if name in self.cache:
            return self.cache[name]
        expected = self.expected.get(name)
        if expected is None:
            raise ValueError(f"Unreceipted dependency: {name}")
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Dependency path escapes source directory")
        size = path.stat().st_size
        if size <= 0 or size > MAX_FILE or size != expected["size_bytes"] or self.total + size > MAX_TOTAL:
            raise ValueError("Source length or aggregate byte budget mismatch")
        with path.open("rb") as source:
            data = source.read(size + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError(f"Source hash mismatch: {name}")
        self.total += size
        self.cache[name] = data
        return data


def export(receipt_path, output):
    local = Path(__file__).resolve().parents[1] / ".local"
    output = output.resolve()
    if not output.is_relative_to(local.resolve()) or output == local.resolve() or output.exists():
        raise ValueError("Output must be a new directory under workspace .local")
    if receipt_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Receipt exceeds 2 MiB")
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes.decode("utf-8-sig"))
    if receipt.get("client_version") != "1.60.1.70888" or receipt.get("product") != "wowf" or receipt.get("exit_code") != 0 or receipt.get("status") not in ("asset_probe_complete", "asset_probe_incomplete") or receipt.get("offline_storage_required") is not True:
        raise ValueError("Expected matching offline build70888 receipt")
    sources = Sources(receipt_path.parent, receipt)
    manifest = json.loads(sources.read("collision-assets.json"))
    if manifest.get("schema_version") != 1 or manifest.get("build") != 70888:
        raise ValueError("Unsupported dependency manifest")
    assets = manifest.get("assets", [])
    if not isinstance(assets, list) or len(assets) > 4096:
        raise ValueError("Invalid dependency count")
    issues = list(manifest.get("issues", []))
    lookup = {}
    decoded = {}
    group_names = {}
    root_doodads = {}
    group_details = {}
    required_dependencies = set()
    reports = []
    decoded_vertices = 0
    decoded_triangles = 0
    for asset in assets:
        key = (asset["kind"], asset["file_data_id"], asset["name"])
        if key in lookup or asset["kind"] not in ("m2", "wmo", "wmo_group"):
            raise ValueError("Duplicate or invalid dependency identity")
        lookup[key] = asset
        report = {**asset, "decoded": False}
        reports.append(report)
        try:
            if asset["error"] or not asset["size_bytes"]:
                raise ValueError(asset["error"] or "Dependency not extracted")
            data = sources.read(asset["path"])
            if len(data) != asset["size_bytes"]:
                raise ValueError("Dependency manifest size mismatch")
            report["sha256"] = hashlib.sha256(data).hexdigest()
            if asset["kind"] == "wmo":
                parts = chunk_map(data)
                if integer(parts.get("MVER", b"")) != 17 or len(parts.get("MOHD", b"")) != 64:
                    raise ValueError("Invalid WMO root header")
                count = integer(parts["MOHD"], 4)
                group_ids = [item[0] for item in array(parts.get("GFID", b""), "<I", 16384)]
                if count > 1024 or len(group_ids) < count or any(not group for group in group_ids[:count]):
                    raise ValueError("Invalid WMO base LOD group IDs")
                names = bytes(parts.get("MOGN", b""))
                for group in group_ids[:count]:
                    if group in group_names and group_names[group] != names:
                        raise ValueError("Shared WMO group has conflicting root name tables")
                    group_names[group] = names
                root = decode_doodads(parts)
                required_dependencies.update(("wmo_group", group, "") for group in group_ids[:count])
                report.update({"groups": group_ids[:count], "doodad_records": len(root["records"]), "doodad_sets": len(root["sets"]), "header_doodad_records": root["header_records"], "header_doodad_sets": root["header_sets"]})
                root_doodads[key] = root
                decoded[key] = group_ids[:count]
            else:
                if asset["kind"] == "wmo_group" and asset["file_data_id"] not in group_names:
                    raise ValueError("WMO group has no previously decoded parent root")
                points, faces, details = decode_m2(data) if asset["kind"] == "m2" else decode_group(data, group_names[asset["file_data_id"]])
                decoded_vertices += len(points)
                decoded_triangles += len(faces)
                if decoded_vertices > MAX_OUTPUT or decoded_triangles > MAX_OUTPUT:
                    raise ValueError("Decoded mesh aggregate budget exceeded")
                decoded[key] = (points, faces)
                report.update({"vertices": len(points), "triangles": len(faces), **details})
                if asset["kind"] == "wmo_group":
                    group_details[key] = details
            report["decoded"] = True
        except (ValueError, OSError, struct.error, UnicodeError) as error:
            report["decode_error"] = str(error)
            issues.append(f"{asset['path']}: {error}")
    all_placements = []
    for name in ("objects0.adt", "objects1.adt"):
        if name in sources.expected:
            all_placements.extend(placements(sources.read(name), name))
    if not any(name in sources.expected for name in ("objects0.adt", "objects1.adt")):
        raise ValueError("No receipted object ADTs")
    unique = {}
    for placement in all_placements:
        key = (placement["kind"], placement["unique_id"])
        if key in unique:
            if {name: value for name, value in placement.items() if name != "source"} != {name: value for name, value in unique[key].items() if name != "source"}:
                raise ValueError("Conflicting duplicate placement ID")
        else:
            unique[key] = placement
        required_dependencies.add((placement["kind"], placement["file_data_id"], placement["name"]))
    embedded = []
    selections = []
    for parent in unique.values():
        key = (parent["kind"], parent["file_data_id"], parent["name"])
        if key not in root_doodads or parent["flags"] & 1:
            continue
        root = root_doodads[key]
        references = set()
        raw_references = 0
        missing_groups = []
        for group in decoded[key]:
            details = group_details.get(("wmo_group", group, ""))
            if details is None:
                missing_groups.append(group)
                continue
            if any(index >= len(root["records"]) for index in details["doodad_indices"]):
                raise ValueError(f"WMO {key[1]} group {group}: MODR outside MODD array")
            if "excluded" not in details:
                raw_references += len(details["doodad_indices"])
                references.update(details["doodad_indices"])
        selected, set_indices = active_doodads(root, references, parent["doodad_set"])
        if len(embedded) + len(selected) > 100000:
            raise ValueError("Selected embedded instance count exceeds 100000 before output")
        selection = {"parent_unique_id": parent["unique_id"], "root_file_data_id": key[1], "requested_set": parent["doodad_set"], "active_sets": set_indices, "selected_set_out_of_range": parent["doodad_set"] >= len(root["sets"]) and parent["doodad_set"] != 0, "modr_records": raw_references, "unique_modr_references": len(references), "selected_instances": len(selected), "missing_groups": missing_groups, "selection_complete": not missing_groups, "missing_required_instances": []}
        selections.append(selection)
        for index in selected:
            doodad = root["records"][index]
            model_id = root["model_ids"][doodad[0] & 0xFFFFFF]
            dependency = ("m2", model_id, "")
            required_dependencies.add(dependency)
            if not model_id or dependency not in decoded:
                selection["missing_required_instances"].append(index)
                issues.append(f"WMO {key[1]} placement {parent['unique_id']} required doodad {index}: missing decoded M2 {model_id}")
            embedded.append(doodad_placement(parent, doodad, index, model_id))
    missing_dependencies = sorted(required_dependencies - lookup.keys())
    for missing in missing_dependencies:
        issues.append(f"Required dependency absent from extraction manifest: {missing}")
    instances = list(unique.values()) + embedded
    planned_vertices = 0
    planned_triangles = 0
    for placement in instances:
        key = (placement["kind"], placement["file_data_id"], placement["name"])
        if placement["kind"] == "wmo" and placement["flags"] & 1:
            continue
        mesh_keys = [("wmo_group", group, "") for group in decoded[key]] if key in decoded and placement["kind"] == "wmo" else [key]
        for mesh_key in mesh_keys:
            if mesh_key in decoded:
                planned_vertices += len(decoded[mesh_key][0])
                planned_triangles += len(decoded[mesh_key][1])
    if planned_vertices > MAX_OUTPUT or planned_triangles > MAX_OUTPUT:
        raise ValueError(f"Instanced mesh output budget {MAX_OUTPUT} exceeded before output: {planned_vertices} vertices, {planned_triangles} triangles, {len(embedded)} selected doodads")
    output.mkdir(parents=True)
    written_vertices = 0
    written_triangles = 0
    placed = []
    with (output / "collision.obj").open("x", encoding="ascii", newline="\n") as model:
        for placement in instances:
            key = (placement["kind"], placement["file_data_id"], placement["name"])
            item = {**placement, "geometry_exported": False, "vertices": 0, "triangles": 0}
            placed.append(item)
            if placement["kind"] == "wmo" and placement["flags"] & 1:
                item["excluded"] = "path_only_WMO_not_static_geometry"
                issues.append(f"Path-only WMO placement excluded: {placement['unique_id']}")
                continue
            mesh_keys = [("wmo_group", group, "") for group in decoded[key]] if key in decoded and placement["kind"] == "wmo" else [key]
            missing = [mesh for mesh in mesh_keys if mesh not in decoded]
            if missing:
                item["missing_meshes"] = missing
                issues.append(f"Placement {placement['unique_id']} missing {len(missing)} decoded meshes")
            for mesh_key in mesh_keys:
                if mesh_key not in decoded:
                    continue
                points, faces = decoded[mesh_key]
                if written_vertices + len(points) > MAX_OUTPUT or written_triangles + len(faces) > MAX_OUTPUT:
                    raise ValueError("Instanced mesh output budget exceeded; partial OBJ is not usable")
                model.write(f"o diagnostic_{placement['kind']}_{placement['unique_id']}_{mesh_key[1]}\n")
                for point in points:
                    world_x, world_y, world_z = transform(point, placement)
                    model.write(f"v {world_x:.7f} {world_y:.7f} {world_z:.7f}\n")
                for first, second, third in faces:
                    model.write(f"f {written_vertices + first + 1} {written_vertices + second + 1} {written_vertices + third + 1}\n")
                written_vertices += len(points)
                written_triangles += len(faces)
                item["vertices"] += len(points)
                item["triangles"] += len(faces)
            item["geometry_exported"] = not missing
    obj = output / "collision.obj"
    with obj.open("rb") as obj_stream:
        obj_hash = hashlib.file_digest(obj_stream, "sha256").hexdigest()
    result = {
        "schema_version": 1,
        "client_version": receipt["client_version"],
        "product": "wowf",
        "status": "diagnostic_incomplete",
        "navigation_ready": False,
        "collision_complete": False,
        "dependencies_complete": manifest.get("dependencies_complete") is True and not missing_dependencies and all(report.get("sha256") for report in reports),
        "source_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "reference_revision": "3da55c1d62db78bb89c98e870645fff17b3ec80c",
        "tile_x": receipt.get("tile_x"),
        "tile_y": receipt.get("tile_y"),
        "coordinate_frame": "trinity_reference_wow_world_xyz_not_verified_in_game",
        "transform": "M2 source vertices unchanged after reference's two canceling axis conversions; reverse M2 triangle winding. R=Rz(rot.y)*Ry(rot.x)*Rx(rot.z); internal=(pos.z,pos.x,pos.y)+R*(scale*vertex); world=(MID-internal.x,MID-internal.y,internal.z)",
        "vertices": written_vertices,
        "triangles": written_triangles,
        "placement_records": len(all_placements),
        "unique_placements": len(unique),
        "placements_exported": sum(item["geometry_exported"] for item in placed if "doodad_index" not in item),
        "instances_exported": sum(item["geometry_exported"] for item in placed),
        "embedded_vertices": sum(item["vertices"] for item in placed if "doodad_index" in item),
        "embedded_triangles": sum(item["triangles"] for item in placed if "doodad_index" in item),
        "embedded_doodad_records": sum(len(root["records"]) for root in root_doodads.values()),
        "embedded_instances_selected": len(embedded),
        "embedded_instances_exported": sum(item["geometry_exported"] for item in placed if "doodad_index" in item),
        "embedded_selection_complete": all(selection["selection_complete"] and not selection["missing_required_instances"] for selection in selections) and all((placement["kind"], placement["file_data_id"], placement["name"]) in root_doodads for placement in unique.values() if placement["kind"] == "wmo" and not placement["flags"] & 1),
        "embedded_transform": "Pinned ExtractSet: translation=P+Rparent*MODD.position; Q normalized; EulerXYZ(Q*Rparent) stored as (Y,Z,X), reconstructed ZYX by ModelInstance/mmaps. Scale=MODD.scale only; parent MODF scale is NOT applied by pinned extractor.",
        "doodad_selections": selections,
        "assets_read": len(sources.cache),
        "source_bytes_read": sources.total,
        "obj_sha256": obj_hash,
        "obj_size_bytes": obj.stat().st_size,
        "excluded": [
            "ADT and WMO liquids and liquid navigation",
            "neighbor tile and map-wide coverage",
            "dynamic obstacles and destructible states",
            "live coordinate validation and navigable mesh generation",
        ],
        "issues": issues,
        "assets": reports,
        "placements": placed,
    }
    (output / "collision.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {name: value for name, value in result.items() if name not in ("assets", "placements", "transform")}


def main():
    parser = argparse.ArgumentParser(description="Export bounded diagnostic placement collision, never a navigation-ready mesh")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(export(arguments.receipt.resolve(), arguments.output), indent=2))


if __name__ == "__main__":
    main()
