from vivipi.core.execution import HttpResponseResult, PingProbeResult, execute_check
from vivipi.core.models import CheckDefinition, CheckType, Status


def make_definition(identifier: str, check_type: CheckType) -> CheckDefinition:
    return CheckDefinition(
        identifier=identifier,
        name=identifier.title(),
        check_type=check_type,
        target="http://example.invalid/health" if check_type != CheckType.PING else "192.168.1.1",
        interval_s=15,
        timeout_s=10,
        method="GET",
        service_prefix="adb" if check_type == CheckType.SERVICE else None,
    )


def test_execute_check_maps_ping_success_and_failure_without_diagnostics():
    definition = make_definition("router", CheckType.PING)

    ok = execute_check(
        definition,
        observed_at_s=10.0,
        ping_runner=lambda target, timeout_s: PingProbeResult(ok=True, latency_ms=12.3, details="reachable"),
        http_runner=None,
    )
    failed = execute_check(
        definition,
        observed_at_s=20.0,
        ping_runner=lambda target, timeout_s: PingProbeResult(ok=False, latency_ms=None, details="timeout"),
        http_runner=None,
    )

    assert ok.observations[0].status == Status.OK
    assert ok.diagnostics == ()
    assert failed.observations[0].status == Status.FAIL
    assert failed.observations[0].details == "timeout"


def test_execute_check_maps_rest_status_codes_to_observations():
    definition = make_definition("nas-api", CheckType.REST)

    result = execute_check(
        definition,
        observed_at_s=10.0,
        ping_runner=None,
        http_runner=lambda method, target, timeout_s: HttpResponseResult(
            status_code=503,
            latency_ms=45.0,
            details="HTTP 503",
        ),
    )

    assert result.observations[0].status == Status.FAIL
    assert result.observations[0].latency_ms == 45.0


def test_execute_check_replaces_service_children_on_success():
    definition = make_definition("android-devices", CheckType.SERVICE)

    result = execute_check(
        definition,
        observed_at_s=30.0,
        ping_runner=None,
        http_runner=lambda method, target, timeout_s: HttpResponseResult(
            status_code=200,
            latency_ms=5.0,
            body={
                "checks": [
                    {
                        "name": "Pixel 8 Pro",
                        "status": "OK",
                        "details": "Connected",
                        "latency_ms": 0,
                    }
                ]
            },
        ),
    )

    assert result.replace_source is True
    assert result.observations[0].identifier == "adb:pixel-8-pro"
    assert result.observations[0].source_identifier == "android-devices"


def test_execute_check_reports_service_schema_errors_via_diagnostics():
    definition = make_definition("android-devices", CheckType.SERVICE)

    result = execute_check(
        definition,
        observed_at_s=30.0,
        ping_runner=None,
        http_runner=lambda method, target, timeout_s: HttpResponseResult(
            status_code=200,
            latency_ms=5.0,
            body={"checks": [{"name": "Pixel 8 Pro", "status": "OK"}]},
        ),
    )

    assert result.observations[0].identifier == "android-devices"
    assert result.observations[0].status == Status.FAIL
    assert result.diagnostics[0].code == "SERV"