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
    assert adb_service.route_request("/missing") == (404, {"error": "not_found"})


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
