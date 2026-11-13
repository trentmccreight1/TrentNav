import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORKSPACE = Path(__file__).resolve().parents[1]
LOCAL = (WORKSPACE / ".local").resolve()
BUILD = 70888
MAX_BODY = 32768


def local_file(value):
    path = Path(value).resolve(strict=True)
    if not path.is_relative_to(LOCAL) or not path.is_file():
        raise ValueError("Diagnostic inputs must be files below the workspace .local directory")
    return path


def bounded_read(path, limit):
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input exceeds size limit")
    return data


def point(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Expected three coordinates")
    if any(type(number) not in (int, float) or not math.isfinite(number) or abs(number) > 100000 for number in value):
        raise ValueError("Invalid coordinate")
    return value


def route_request(body):
    lines = body.decode("ascii").splitlines()
    if len(lines) < 8 or lines[0] != "TNAV1 REQUEST" or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", lines[1]):
        raise ValueError("Invalid route envelope")
    build, map_id, count = map(int, lines[2:5])
    if build != BUILD or map_id < 0 or map_id > 100000 or count != 2:
        raise ValueError("Unsupported build, map or endpoint count")
    endpoints = [point([float(part) for part in line.split()]) for line in lines[5:7]]
    zone_count = int(lines[7])
    if zone_count < 0 or zone_count > 256 or len(lines) != 8 + zone_count:
        raise ValueError("Invalid avoid-zone count")
    for line in lines[8:]:
        values = [float(part) for part in line.split()]
        if len(values) != 4:
            raise ValueError("Invalid avoid zone")
        point(values[:3])
        if not math.isfinite(values[3]) or not 0 < values[3] <= 200:
            raise ValueError("Invalid avoid-zone radius")
    return {"request_id": lines[1], "build": build, "map_id": map_id, "endpoints": endpoints}


class DiagnosticDataset:
    def __init__(self, mesh_path, query_path):
        self.mesh = local_file(mesh_path)
        self.query = local_file(query_path)
        mesh_data = bounded_read(self.mesh, 64 * 1024 * 1024)
        if len(mesh_data) < 24 or mesh_data[:8] != b"TNVDIAG1":
            raise ValueError("Only diagnostic mesh artifacts are accepted")
        if struct.unpack_from("<4I", mesh_data, 8) != (1, BUILD, 0, len(mesh_data) - 24):
            raise ValueError("Invalid diagnostic mesh envelope")
        self.mesh_digest = hashlib.sha256(mesh_data).hexdigest()
        self.query_digest = hashlib.sha256(bounded_read(self.query, 64 * 1024 * 1024)).hexdigest()
        self.sample = self.run(None)

    def run(self, endpoints):
        if hashlib.sha256(bounded_read(self.mesh, 64 * 1024 * 1024)).hexdigest() != self.mesh_digest:
            raise ValueError("Mesh changed after startup; restart explicitly")
        if hashlib.sha256(bounded_read(self.query, 64 * 1024 * 1024)).hexdigest() != self.query_digest:
            raise ValueError("Query binary changed after startup; restart explicitly")
        arguments = [str(self.query), str(self.mesh)]
        if endpoints is None:
            arguments.append("--sample")
        else:
            arguments.extend(str(number) for endpoint in endpoints for number in endpoint)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
        result = subprocess.run(arguments, capture_output=True, timeout=10, check=False, **options)
        if result.returncode != 0:
            raise ValueError("No complete diagnostic route; check coordinates, coverage and mesh validity")
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("Query output exceeded limit")
        response = json.loads(result.stdout)
        if response.get("navigation_ready") is not False or response.get("complete") is not True or response.get("build") != BUILD:
            raise ValueError("Invalid diagnostic query result")
        waypoints = response.get("waypoints")
        if not isinstance(waypoints, list) or not 1 <= len(waypoints) <= 4096:
            raise ValueError("Invalid diagnostic waypoint count")
        for waypoint in waypoints:
            point(waypoint)
        return response


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8
    block_on_close = False

    def __init__(self, port, dataset):
        self.dataset = dataset
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "TrentNav/1"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(4)
        super().setup()

    def log_message(self, format_string, *arguments):
        pass

    def respond(self, code, value, plain=False):
        payload = value.encode("utf-8") if plain else json.dumps(value, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8" if plain else "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def trusted_request(self):
        expected = "127.0.0.1:" + str(self.server.server_port)
        if self.client_address[0] != "127.0.0.1" or self.headers.get_all("Host") != [expected] or self.headers.get("Origin"):
            self.respond(403, {"error": "Only direct loopback non-browser requests are accepted"})
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        if self.path == "/v1/health":
            self.health()
        else:
            self.respond(404, {"error": "Unknown endpoint"})

    def health(self):
        dataset = self.server.dataset
        if self.headers.get("Accept") == "text/plain":
            self.respond(200, "TNAV1 HEALTH\n70888\n0\ncollision_coverage_incomplete\n", plain=True)
        else:
            self.respond(200, {
                "protocol": "TNAV1", "build": BUILD, "navigation_ready": False,
                "runtime_route_enabled": False, "reason": "collision_coverage_incomplete",
                "diagnostic_loaded": dataset is not None,
                "mesh_sha256": dataset.mesh_digest if dataset else None,
                "sample": dataset.sample if dataset else None,
            })

    def do_POST(self):
        if not self.trusted_request():
            return
        try:
            sizes = self.headers.get_all("Content-Length", [])
            if len(sizes) != 1 or not sizes[0].isdigit() or self.headers.get("Transfer-Encoding"):
                self.respond(400, {"error": "One Content-Length and no transfer encoding required"})
                return
            size = int(sizes[0])
            if size > MAX_BODY:
                self.respond(413, {"error": "Request too large"})
                return
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError("Truncated body")
            if self.path == "/v1/health":
                if body:
                    raise ValueError("Health body must be empty")
                self.health()
            elif self.path == "/v1/route":
                request = route_request(body)
                self.respond(409, "TNAV1 ERROR\n" + request["request_id"] + "\ncollision_coverage_incomplete\n", plain=True)
            elif self.path == "/v1/diagnostic-route":
                if self.server.dataset is None:
                    self.respond(503, {"error": "No diagnostic dataset loaded"})
                    return
                request = json.loads(body)
                if not isinstance(request, dict) or set(request) != {"start", "end"}:
                    raise ValueError("Expected start and end only; no implicit map coverage")
                endpoints = [point(request["start"]), point(request["end"])]
                self.respond(200, self.server.dataset.run(endpoints))
            else:
                self.respond(404, {"error": "Unknown endpoint"})
        except (ValueError, UnicodeError, OverflowError, OSError, subprocess.TimeoutExpired):
            self.respond(422, {"error": "Invalid request or unavailable complete diagnostic route"})


def main():
    parser = argparse.ArgumentParser(description="Owned loopback navigation transport. Incomplete meshes are diagnostic only.")
    parser.add_argument("--port", type=int, default=17654)
    parser.add_argument("--diagnostic-mesh")
    parser.add_argument("--query", default=str(LOCAL / "native-build/Release/trentnav_mesh_query.exe"))
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    dataset = DiagnosticDataset(arguments.diagnostic_mesh, arguments.query) if arguments.diagnostic_mesh else None
    server = Server(arguments.port, dataset)
    print(json.dumps({"listening": "http://127.0.0.1:" + str(server.server_port), "navigation_ready": False,
                      "diagnostic_loaded": dataset is not None}), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
