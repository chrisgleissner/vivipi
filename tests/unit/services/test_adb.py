from types import SimpleNamespace

from vivipi.services.adb import collect_adb_service_payload, parse_adb_devices
from vivipi.services.adb_service import route_request


def test_parse_adb_devices_ignores_blank_and_banner_lines():
    output = """
* daemon not running; starting now at tcp:5037
List of devices attached
emulator-5554 device product:sdk_gphone model:Pixel_8 device:emu64xa

"""

    devices = parse_adb_devices(output)

    assert len(devices) == 1
    assert devices[0].serial == "emulator-5554"
    assert devices[0].state == "device"


def test_parse_adb_devices_ignores_malformed_lines():
    devices = parse_adb_devices("List of devices attached\nmalformed-line\nserial-01 device\n")

    assert len(devices) == 1
    assert devices[0].serial == "serial-01"


def test_collect_adb_payload_returns_degraded_when_no_devices_are_connected():
    payload = collect_adb_service_payload(
        run_command=lambda command: SimpleNamespace(returncode=0, stdout="List of devices attached\n\n", stderr=""),
    )

    assert payload["checks"][0]["status"] == "DEG"


def test_collect_adb_payload_marks_offline_devices_as_failed():
    payload = collect_adb_service_payload(
        run_command=lambda command: SimpleNamespace(
            returncode=0,
            stdout="List of devices attached\nserial-01 offline transport_id:1\n",
            stderr="",
        ),
    )

    assert payload["checks"][0]["name"] == "serial-01"
    assert payload["checks"][0]["status"] == "FAIL"


def test_collect_adb_payload_normalizes_healthy_device_details():
    payload = collect_adb_service_payload(
        run_command=lambda command: SimpleNamespace(returncode=0, stdout="List of devices attached\nserial-01 device\n", stderr=""),
    )

    assert payload["checks"][0]["details"] == "Connected"


def test_collect_adb_payload_returns_failure_when_the_command_fails():
    payload = collect_adb_service_payload(
        run_command=lambda command: SimpleNamespace(returncode=1, stdout="", stderr="adb missing"),
    )

    assert payload["checks"][0]["status"] == "FAIL"
    assert payload["checks"][0]["details"] == "adb missing"


def test_route_request_serves_health_and_check_routes():
    health_status, health_body = route_request("/health")
    checks_status, checks_body = route_request("/checks", payload_factory=lambda: {"checks": []})
    missing_status, missing_body = route_request("/missing")

    assert health_status == 200
    assert health_body == {"status": "OK"}
    assert checks_status == 200
    assert checks_body == {"checks": []}
    assert missing_status == 404
    assert missing_body == {"error": "not_found"}
