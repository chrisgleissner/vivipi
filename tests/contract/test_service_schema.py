import pytest

from vivipi.core.models import Status
import vivipi.services.schema as schema_module
from vivipi.services.schema import parse_service_payload


def test_parse_service_payload_validates_schema_and_builds_stable_ids():
    payload = {
        "checks": [
            {
                "name": "Pixel 8 Pro",
                "status": "OK",
                "details": "Connected",
                "latency_ms": 0,
            }
        ]
    }

    observations = parse_service_payload(
        payload,
        service_prefix="adb",
        observed_at_s=123.0,
        source_identifier="android-devices",
    )

    assert observations[0].identifier == "adb:pixel-8-pro"
    assert observations[0].status == Status.OK
    assert observations[0].observed_at_s == 123.0
    assert observations[0].source_identifier == "android-devices"


def test_parse_service_payload_casefolds_non_ascii_service_ids_consistently():
    payload = {
        "checks": [
            {
                "name": "Straße",
                "status": "OK",
                "details": "Connected",
                "latency_ms": 0,
            }
        ]
    }

    observations = parse_service_payload(payload, service_prefix="adb")

    assert observations[0].identifier == "adb:strasse"


def test_schema_slugify_falls_back_to_lower_when_casefold_is_not_callable():
    class LegacyText(str):
        casefold = None

    assert schema_module._fold(LegacyText("PIXEL 4")) == "pixel 4"


def test_parse_service_payload_accepts_unknown_status_display():
    payload = {
        "checks": [
            {
                "name": "Router",
                "status": "?",
                "details": "Pending first result",
                "latency_ms": 0,
            }
        ]
    }

    observations = parse_service_payload(payload)

    assert observations[0].status == Status.UNKNOWN


def test_parse_service_payload_rejects_invalid_status_values():
    payload = {
        "checks": [
            {
                "name": "Router",
                "status": "BROKEN",
                "details": "Invalid",
                "latency_ms": 0,
            }
        ]
    }

    with pytest.raises(ValueError):
        parse_service_payload(payload)


def test_parse_service_payload_rejects_duplicate_service_check_ids():
    payload = {
        "checks": [
            {
                "name": "Pixel 8",
                "status": "OK",
                "details": "Connected",
                "latency_ms": 0,
            },
            {
                "name": "Pixel-8",
                "status": "OK",
                "details": "Connected",
                "latency_ms": 0,
            },
        ]
    }

    with pytest.raises(ValueError, match="duplicate"):
        parse_service_payload(payload, service_prefix="adb")


def test_parse_service_payload_rejects_invalid_payload_shapes():
    with pytest.raises(ValueError, match="payload must be an object"):
        parse_service_payload([])

    with pytest.raises(ValueError, match="checks list"):
        parse_service_payload({"checks": {}})

    with pytest.raises(ValueError, match="must be an object"):
        parse_service_payload({"checks": ["bad"]})


def test_parse_service_payload_rejects_invalid_field_types():
    payload = {
        "checks": [
            {
                "name": "Router",
                "status": 123,
                "details": "Connected",
                "latency_ms": 0,
            }
        ]
    }

    with pytest.raises(ValueError, match="status must be a string"):
        parse_service_payload(payload)

    bad_details = {
        "checks": [
            {
                "name": "Router",
                "status": "OK",
                "details": 10,
                "latency_ms": 0,
            }
        ]
    }
    with pytest.raises(ValueError, match="details must be a string"):
        parse_service_payload(bad_details)

    bad_latency = {
        "checks": [
            {
                "name": "Router",
                "status": "OK",
                "details": "Connected",
                "latency_ms": "fast",
            }
        ]
    }
    with pytest.raises(ValueError, match="latency_ms must be numeric"):
        parse_service_payload(bad_latency)

    negative_latency = {
        "checks": [
            {
                "name": "Router",
                "status": "OK",
                "details": "Connected",
                "latency_ms": -1,
            }
        ]
    }
    with pytest.raises(ValueError, match="latency_ms must be non-negative"):
        parse_service_payload(negative_latency)

    bad_name = {
        "checks": [
            {
                "name": "   ",
                "status": "OK",
                "details": "Connected",
                "latency_ms": 0,
            }
        ]
    }
    with pytest.raises(ValueError, match="name must be a non-empty string"):
        parse_service_payload(bad_name)


def test_parse_service_payload_rejects_payloads_that_exceed_the_safe_check_limit():
    payload = {
        "checks": [
            {"name": f"Check {index}", "status": "OK", "details": "ready", "latency_ms": 1.0}
            for index in range(65)
        ]
    }

    with pytest.raises(ValueError, match="maximum service checks"):
        parse_service_payload(payload)
