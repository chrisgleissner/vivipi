from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from vivipi.services.adb import collect_adb_device_status, collect_adb_service_payload
from vivipi.runtime.checks import portable_http_runner, portable_ping_runner, portable_telnet_runner


def _probe_response(status: str, details: str, latency_ms: float | None = None) -> tuple[int, dict[str, object]]:
    normalized = str(status).strip().upper() or "FAIL"
    return (200 if normalized in {"OK", "DEG"} else 503), {
        "status": normalized,
        "details": details,
        "latency_ms": 0 if latency_ms is None else latency_ms,
    }


def _probe_status(result) -> str:
    status = getattr(result, "status", None)
    if status is None:
        return "OK" if result.ok else "FAIL"
    return str(getattr(status, "value", status)).strip().upper() or "FAIL"


def _query_value(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    if not values:
        return default
    return str(values[0]).strip()


def route_request(path: str, payload_factory=collect_adb_service_payload) -> tuple[int, dict[str, object]]:
    parsed = urlparse(path)
    route = parsed.path
    query = parse_qs(parsed.query)
    probe_prefix = "/vivipi/probe"
    if route in {"/health", "/healthz"}:
        return 200, {"status": "OK"}
    if route == "/checks":
        return 200, payload_factory()
    if route.startswith("/adb/") or route.startswith(f"{probe_prefix}/adb/"):
        serial = route.rsplit("/", 1)[-1].strip()
        if not serial:
            return 404, {"error": "not_found"}
        return collect_adb_device_status(serial, target_name="PIXEL4 ADB")
    if route in {"/probe/ping", f"{probe_prefix}/ping"}:
        target = _query_value(query, "target")
        timeout_s = int(_query_value(query, "timeout_s", "10") or "10")
        result = portable_ping_runner(target, timeout_s)
        return _probe_response(_probe_status(result), result.details, result.latency_ms)
    if route in {"/probe/http", f"{probe_prefix}/http"}:
        target = _query_value(query, "target")
        method = _query_value(query, "method", "GET") or "GET"
        timeout_s = int(_query_value(query, "timeout_s", "10") or "10")
        result = portable_http_runner(method, target, timeout_s)
        status = "OK" if result.status_code is not None and 200 <= int(result.status_code) < 400 else "FAIL"
        return _probe_response(status, result.details, result.latency_ms)
    if route in {"/probe/telnet", f"{probe_prefix}/telnet"}:
        target = _query_value(query, "target")
        timeout_s = int(_query_value(query, "timeout_s", "10") or "10")
        result = portable_telnet_runner(target, timeout_s)
        return _probe_response(_probe_status(result), result.details, result.latency_ms)
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
