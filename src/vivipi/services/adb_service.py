from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from vivipi.services.adb import collect_adb_service_payload


def route_request(path: str, payload_factory=collect_adb_service_payload) -> tuple[int, dict[str, object]]:
    route = urlparse(path).path
    if route in {"/health", "/healthz"}:
        return 200, {"status": "OK"}
    if route == "/checks":
        return 200, payload_factory()
    return 404, {"error": "not_found"}


def build_handler(payload_factory=collect_adb_service_payload):
    class ViviPiHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            status_code, payload = route_request(self.path, payload_factory=payload_factory)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_string, *args):
            return None

    return ViviPiHandler


def serve(host: str = "127.0.0.1", port: int = 8080, payload_factory=collect_adb_service_payload):
    server = ThreadingHTTPServer((host, port), build_handler(payload_factory))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the default ViviPi ADB-backed service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
