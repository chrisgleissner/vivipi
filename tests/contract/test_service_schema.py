import pytest

from vivipi.core.models import Status
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

    observations = parse_service_payload(payload, service_prefix="adb", observed_at_s=123.0)

    assert observations[0].identifier == "adb:pixel-8-pro"
    assert observations[0].status == Status.OK
    assert observations[0].observed_at_s == 123.0


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
