import io

from vivipi.services import adb_service


def test_adb_service_main_parses_arguments_and_calls_serve(monkeypatch):
    called = {}

    def fake_serve(host: str, port: int, payload_factory=None):
        called["host"] = host
        called["port"] = port
        called["payload_factory"] = payload_factory

    monkeypatch.setattr(adb_service, "serve", fake_serve)

    exit_code = adb_service.main(["--host", "0.0.0.0", "--port", "9090"])

    assert exit_code == 0
    assert called["host"] == "0.0.0.0"
    assert called["port"] == 9090


def test_build_handler_overrides_logging():
    handler = adb_service.build_handler(payload_factory=lambda: {"checks": []})

    assert handler.log_message is not None


def test_route_request_handles_health_checks_and_missing_routes():
    assert adb_service.route_request("/health") == (200, {"status": "OK"})
    assert adb_service.route_request("/checks", payload_factory=lambda: {"checks": []}) == (200, {"checks": []})
    assert adb_service.route_request("/missing") == (404, {"error": "not_found"})


def test_route_request_handles_probe_routes(monkeypatch):
    monkeypatch.setattr(adb_service, "portable_ping_runner", lambda target, timeout_s: type("Result", (), {"ok": True, "details": "reachable", "latency_ms": 5.0})())
    monkeypatch.setattr(adb_service, "portable_http_runner", lambda method, target, timeout_s: type("Result", (), {"status_code": 200, "details": "HTTP 200", "latency_ms": 10.0})())
    monkeypatch.setattr(adb_service, "portable_telnet_runner", lambda target, timeout_s: type("Result", (), {"ok": False, "details": "login failed", "latency_ms": 15.0})())

    ping_status, ping_body = adb_service.route_request("/probe/ping?target=192.168.1.13")
    http_status, http_body = adb_service.route_request("/probe/http?target=http%3A%2F%2F192.168.1.13%2Fv1%2Fversion")
    telnet_status, telnet_body = adb_service.route_request("/probe/telnet?target=192.168.1.13:23")
    namespaced_ping_status, namespaced_ping_body = adb_service.route_request("/vivipi/probe/ping?target=192.168.1.13")
    namespaced_http_status, namespaced_http_body = adb_service.route_request("/vivipi/probe/http?target=http%3A%2F%2F192.168.1.13%2Fv1%2Fversion")
    namespaced_telnet_status, namespaced_telnet_body = adb_service.route_request("/vivipi/probe/telnet?target=192.168.1.13:23")

    assert ping_status == 200
    assert ping_body["status"] == "OK"
    assert http_status == 200
    assert http_body["details"] == "HTTP 200"
    assert telnet_status == 503
    assert telnet_body["status"] == "FAIL"
    assert namespaced_ping_status == 200
    assert namespaced_ping_body["status"] == "OK"
    assert namespaced_http_status == 200
    assert namespaced_http_body["details"] == "HTTP 200"
    assert namespaced_telnet_status == 503
    assert namespaced_telnet_body["status"] == "FAIL"


def test_route_request_handles_namespaced_adb_route(monkeypatch):
    monkeypatch.setattr(
        adb_service,
        "collect_adb_device_status",
        lambda serial, target_name: (200, {"serial": serial, "name": target_name, "status": "OK"}),
    )

    status, body = adb_service.route_request("/vivipi/probe/adb/9B081FFAZ001WX")

    assert status == 200
    assert body == {"serial": "9B081FFAZ001WX", "name": "PIXEL4 ADB", "status": "OK"}


def test_build_handler_writes_json_response_body():
    handler_type = adb_service.build_handler(payload_factory=lambda: {"checks": []})
    handler = object.__new__(handler_type)
    handler.path = "/checks"
    handler.wfile = io.BytesIO()
    calls = {"headers": []}
    handler.send_response = lambda status_code: calls.__setitem__("status", status_code)
    handler.send_header = lambda key, value: calls["headers"].append((key, value))
    handler.end_headers = lambda: calls.__setitem__("ended", True)

    handler.do_GET()

    assert calls["status"] == 200
    assert ("Content-Type", "application/json") in calls["headers"]
    assert calls["ended"] is True
    assert handler.wfile.getvalue() == b'{"checks": []}'


def test_serve_closes_the_server(monkeypatch):
    called = {"closed": False, "served": False}

    class FakeServer:
        def __init__(self, address, handler):
            self.address = address
            self.handler = handler

        def serve_forever(self):
            called["served"] = True

        def server_close(self):
            called["closed"] = True

    monkeypatch.setattr(adb_service, "ThreadingHTTPServer", FakeServer)

    adb_service.serve(host="127.0.0.1", port=8080, payload_factory=lambda: {"checks": []})

    assert called == {"served": True, "closed": True}
