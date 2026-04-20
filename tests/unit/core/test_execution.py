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
    assert ok.probe_latency_ms == 12.3
    assert failed.observations[0].status == Status.FAIL
    assert failed.observations[0].details == "timeout"
    assert failed.probe_latency_ms is None


def test_execute_check_maps_http_status_codes_to_observations():
    definition = make_definition("nas-api", CheckType.HTTP)

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
    assert result.probe_latency_ms == 45.0


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
    assert result.probe_latency_ms == 5.0


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
    assert result.probe_latency_ms == 5.0


def test_execute_check_reports_ping_and_http_executor_failures_via_diagnostics():
    ping_definition = make_definition("router", CheckType.PING)
    http_definition = make_definition("nas-api", CheckType.HTTP)

    ping_result = execute_check(
        ping_definition,
        observed_at_s=10.0,
        ping_runner=lambda target, timeout_s: (_ for _ in ()).throw(RuntimeError("boom")),
        http_runner=None,
    )
    http_result = execute_check(
        http_definition,
        observed_at_s=10.0,
        ping_runner=None,
        http_runner=lambda method, target, timeout_s: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert ping_result.diagnostics[0].code == "PING"
    assert ping_result.observations[0].details == "executor error"
    assert http_result.diagnostics[0].code == "HTTP"
    assert ping_result.probe_latency_ms is None
    assert http_result.probe_latency_ms is None


def test_execute_check_maps_ftp_and_telnet_probe_results():
    ftp_definition = CheckDefinition(
        identifier="nas-ftp",
        name="NAS FTP",
        check_type=CheckType.FTP,
        target="ftp://nas.example.local",
        interval_s=15,
        timeout_s=10,
        username="admin",
        password="secret",
    )
    telnet_definition = CheckDefinition(
        identifier="switch-console",
        name="Switch Console",
        check_type=CheckType.TELNET,
        target="telnet://switch.example.local",
        interval_s=15,
        timeout_s=10,
        username="ops",
        password="pw",
    )
    ftp_calls = []
    telnet_calls = []

    ftp_result = execute_check(
        ftp_definition,
        observed_at_s=10.0,
        ping_runner=None,
        http_runner=None,
        ftp_runner=lambda target, timeout_s, username, password: (
            ftp_calls.append((target, timeout_s, username, password))
            or PingProbeResult(ok=True, latency_ms=21.0, details="listed 3 entries")
        ),
        telnet_runner=None,
    )
    telnet_result = execute_check(
        telnet_definition,
        observed_at_s=11.0,
        ping_runner=None,
        http_runner=None,
        ftp_runner=None,
        telnet_runner=lambda target, timeout_s, username, password: (
            telnet_calls.append((target, timeout_s, username, password))
            or PingProbeResult(ok=False, latency_ms=8.0, details="login failed")
        ),
    )

    assert ftp_calls == [("ftp://nas.example.local", 10, "admin", "secret")]
    assert ftp_result.observations[0].status == Status.OK
    assert ftp_result.observations[0].details == "listed 3 entries"
    assert ftp_result.probe_latency_ms == 21.0
    assert telnet_calls == [("telnet://switch.example.local", 10, "ops", "pw")]
    assert telnet_result.observations[0].status == Status.FAIL
    assert telnet_result.observations[0].details == "login failed"
    assert telnet_result.probe_latency_ms == 8.0


def test_execute_check_preserves_explicit_telnet_degraded_status():
    telnet_definition = CheckDefinition(
        identifier="switch-console",
        name="Switch Console",
        check_type=CheckType.TELNET,
        target="telnet://switch.example.local",
        interval_s=15,
        timeout_s=10,
    )

    result = execute_check(
        telnet_definition,
        observed_at_s=11.0,
        ping_runner=None,
        http_runner=None,
        ftp_runner=None,
        telnet_runner=lambda target, timeout_s, username, password: PingProbeResult(
            ok=False,
            status=Status.DEG,
            latency_ms=8.0,
            details="connected-no-telnet-data",
            metadata={
                7: "normalized-key",
                "close_reason": "idle-timeout",
                "session_duration_ms": 600.0,
                "handshake_detected": False,
                "ignored": None,
            },
        ),
    )

    assert result.observations[0].status == Status.DEG
    assert result.observations[0].details == "connected-no-telnet-data"
    assert result.probe_metadata == {
        "7": "normalized-key",
        "close_reason": "idle-timeout",
        "session_duration_ms": 600.0,
        "handshake_detected": False,
    }


def test_execute_check_passes_http_password_to_runner():
    definition = CheckDefinition(
        identifier="u64-rest",
        name="U64 REST",
        check_type=CheckType.HTTP,
        target="http://192.168.1.13/v1/version",
        interval_s=15,
        timeout_s=10,
        password="secret",
    )
    calls = []

    result = execute_check(
        definition,
        observed_at_s=12.0,
        ping_runner=None,
        http_runner=lambda method, target, timeout_s, username, password: (
            calls.append((method, target, timeout_s, username, password))
            or HttpResponseResult(status_code=200, details="HTTP 200", latency_ms=14.0)
        ),
        ftp_runner=None,
        telnet_runner=None,
    )

    assert calls == [("GET", "http://192.168.1.13/v1/version", 10, None, "secret")]
    assert result.observations[0].status == Status.OK
    assert result.probe_latency_ms == 14.0


def test_execute_check_reports_ftp_and_telnet_executor_failures_via_diagnostics():
    ftp_definition = CheckDefinition(
        identifier="nas-ftp",
        name="NAS FTP",
        check_type=CheckType.FTP,
        target="ftp://nas.example.local",
    )
    telnet_definition = CheckDefinition(
        identifier="switch-console",
        name="Switch Console",
        check_type=CheckType.TELNET,
        target="telnet://switch.example.local",
    )

    ftp_result = execute_check(
        ftp_definition,
        observed_at_s=10.0,
        ping_runner=None,
        http_runner=None,
        ftp_runner=lambda target, timeout_s, username, password: (_ for _ in ()).throw(RuntimeError("boom")),
        telnet_runner=None,
    )
    telnet_result = execute_check(
        telnet_definition,
        observed_at_s=10.0,
        ping_runner=None,
        http_runner=None,
        ftp_runner=None,
        telnet_runner=lambda target, timeout_s, username, password: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert ftp_result.diagnostics[0].code == "FTP"
    assert ftp_result.observations[0].details == "executor error"
    assert telnet_result.diagnostics[0].code == "TELN"
    assert telnet_result.observations[0].details == "executor error"
    assert ftp_result.probe_latency_ms is None
    assert telnet_result.probe_latency_ms is None


def test_execute_check_handles_service_request_failures_and_non_2xx_responses():
    definition = make_definition("android-devices", CheckType.SERVICE)

    failed_request = execute_check(
        definition,
        observed_at_s=10.0,
        ping_runner=None,
        http_runner=lambda method, target, timeout_s: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    failed_response = execute_check(
        definition,
        observed_at_s=10.0,
        ping_runner=None,
        http_runner=lambda method, target, timeout_s: HttpResponseResult(status_code=503, details="HTTP 503"),
    )

    assert failed_request.replace_source is True
    assert failed_request.diagnostics[0].code == "SERV"
    assert failed_response.replace_source is True
    assert failed_response.observations[0].details == "HTTP 503"
    assert failed_request.probe_latency_ms is None
    assert failed_response.probe_latency_ms is None
