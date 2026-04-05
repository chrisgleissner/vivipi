import sys
from types import SimpleNamespace

import pytest

from vivipi.core.execution import PingProbeResult
from vivipi.core.models import CheckType, Status
from vivipi.runtime.checks import build_executor, build_runtime_definitions, portable_http_runner, portable_ping_runner


def test_build_runtime_definitions_reads_runtime_config_shape():
    definitions = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "router",
                    "name": "Router",
                    "type": "PING",
                    "target": "192.168.1.1",
                    "interval_s": 15,
                    "timeout_s": 10,
                }
            ]
        }
    )

    assert definitions[0].check_type == CheckType.PING
    assert definitions[0].identifier == "router"


def test_build_runtime_definitions_rejects_invalid_shapes_and_normalizes_blank_prefixes():
    with pytest.raises(ValueError, match="checks list"):
        build_runtime_definitions({"checks": {}})

    with pytest.raises(ValueError, match="must be objects"):
        build_runtime_definitions({"checks": ["bad"]})

    definitions = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "service",
                    "name": "Service",
                    "type": "SERVICE",
                    "target": "http://192.0.2.10:8080/checks",
                    "service_prefix": "   ",
                }
            ]
        }
    )

    assert definitions[0].service_prefix is None
    assert definitions[0].method == "GET"


def test_build_executor_uses_supplied_runners():
    definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "router",
                    "name": "Router",
                    "type": "PING",
                    "target": "192.168.1.1",
                    "interval_s": 15,
                    "timeout_s": 10,
                }
            ]
        }
    )[0]

    executor = build_executor(
        ping_runner=lambda target, timeout_s: PingProbeResult(ok=True, latency_ms=12.0, details="reachable"),
        http_runner=None,
    )

    result = executor(definition, 10.0)

    assert result.observations[0].status == Status.OK


def test_portable_http_runner_prefers_urequests_when_available(monkeypatch):
    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: {"checks": []},
        text="{\"checks\": []}",
        close=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "urequests", SimpleNamespace(request=lambda method, target, timeout: fake_response))

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.status_code == 200
    assert result.body == {"checks": []}


def test_portable_http_runner_falls_back_to_response_text_when_json_parsing_fails(monkeypatch):
    def raise_value_error():
        raise ValueError("bad json")

    fake_response = SimpleNamespace(
        status_code=200,
        json=raise_value_error,
        text="plain text",
        close=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "urequests", SimpleNamespace(request=lambda method, target, timeout: fake_response))

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.body == "plain text"


def test_portable_ping_runner_uses_uping_when_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "uping", SimpleNamespace(ping=lambda *args, **kwargs: (1, 1, 12.0, 12.0)))

    result = portable_ping_runner("192.168.1.1", 10)

    assert result.ok is True
    assert result.details == "reachable"


def test_portable_ping_runner_uses_subprocess_fallback(monkeypatch):
    monkeypatch.delitem(sys.modules, "uping", raising=False)

    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="network unreachable"),
    )

    result = portable_ping_runner("192.168.1.1", 10)

    assert result.ok is False
    assert result.details == "network unreachable"