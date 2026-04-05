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
