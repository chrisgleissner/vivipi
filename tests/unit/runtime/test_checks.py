import sys
import builtins
from types import SimpleNamespace

import pytest

from vivipi.core.execution import CheckExecutionResult, PingProbeResult
from vivipi.core.models import CheckObservation, CheckType, Status
import vivipi.runtime.checks as runtime_checks
from vivipi.runtime.checks import (
    _close_socket,
    _classify_network_error,
    _ftp_nlst_names,
    _ftp_read_response,
    _ftp_parse_pasv,
    _format_network_error,
    _looks_like_telnet_output,
    _normalize_error_text,
    _open_socket,
    _parse_socket_target,
    _read_until_markers,
    _recv_all,
    _runtime_optional_auth,
    _sleep_ms,
    _telnet_strip_negotiation,
    build_executor,
    build_runtime_definitions,
    load_runtime_checks,
    portable_ftp_runner,
    portable_http_runner,
    portable_ping_runner,
    portable_telnet_runner,
)


class FakeSocket:
    def __init__(self, responses):
        self._responses = [response if isinstance(response, bytes) else response.encode("utf-8") for response in responses]
        self.sent = []
        self.closed = False

    def recv(self, _size):
        if not self._responses:
            return b""
        return self._responses.pop(0)

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class CloseErrorSocket(FakeSocket):
    def close(self):
        raise OSError("close failed")


class ShutdownRecordingSocket(FakeSocket):
    def __init__(self, responses):
        super().__init__(responses)
        self.shutdown_calls = []

    def shutdown(self, how):
        self.shutdown_calls.append(how)


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


def test_load_runtime_checks_reads_yaml_for_runtime_validation(tmp_path):
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text(
        """
checks:
  - name: NAS FTP
    type: ftp
    target: 192.168.1.167
    username: ${VIVIPI_NETWORK_USERNAME}
    password: ${VIVIPI_NETWORK_PASSWORD}
""".strip(),
        encoding="utf-8",
    )

    definitions = load_runtime_checks(checks_path, env={})

    assert definitions[0].identifier == "nas-ftp"
    assert definitions[0].username is None
    assert definitions[0].password is None


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


def test_probe_end_helper_normalization_covers_enum_name_and_service_fallback_paths():
    definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "service",
                    "name": "Service",
                    "type": "SERVICE",
                    "target": "http://service.local/checks",
                    "service_prefix": "adb",
                }
            ]
        }
    )[0]

    class FakeEnumLike:
        value = "<property>"
        name = "http"

        def __str__(self):
            return "ignored"

    assert runtime_checks._check_type_name(SimpleNamespace(check_type=FakeEnumLike())) == "HTTP"
    assert runtime_checks._status_text(FakeEnumLike()) == "http"
    assert runtime_checks._probe_end_status(definition, SimpleNamespace(observations=(), replace_source=True)) == "OK"
    assert runtime_checks._probe_end_detail(definition, SimpleNamespace(observations=(), replace_source=True)) == ""
    assert runtime_checks._probe_end_latency_ms(definition, SimpleNamespace(observations=(), probe_latency_ms=None)) is None

    fallback_definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "router",
                    "name": "Router",
                    "type": "PING",
                    "target": "192.168.1.1",
                }
            ]
        }
    )[0]
    fallback_result = SimpleNamespace(
        observations=(SimpleNamespace(identifier="other", status=Status.FAIL, details="timeout", latency_ms=9.0),),
        probe_latency_ms=None,
    )

    assert runtime_checks._probe_end_status(fallback_definition, fallback_result) == "FAIL"
    assert runtime_checks._probe_end_detail(fallback_definition, fallback_result) == "timeout"
    assert runtime_checks._probe_end_latency_ms(fallback_definition, fallback_result) == 9.0
    assert runtime_checks._probe_end_status(fallback_definition, SimpleNamespace(observations=(), probe_latency_ms=None)) == "?"
    assert fallback_definition.method == "GET"


def test_probe_end_helpers_cover_string_fallbacks_and_empty_non_service_paths():
    class StringOnly:
        value = None
        name = None

        def __str__(self):
            return "mystery"

    fallback_definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "router",
                    "name": "Router",
                    "type": "PING",
                    "target": "192.168.1.1",
                }
            ]
        }
    )[0]
    empty_result = SimpleNamespace(observations=(), probe_latency_ms=None)

    assert runtime_checks._check_type_name(SimpleNamespace(check_type=StringOnly())) == "MYSTERY"
    assert runtime_checks._status_text(StringOnly()) == "mystery"
    assert runtime_checks._probe_end_detail(fallback_definition, empty_result) == ""
    assert runtime_checks._probe_end_latency_ms(fallback_definition, empty_result) is None


def test_maybe_collect_gc_handles_mem_free_errors_and_skips_when_memory_is_sufficient(monkeypatch):
    class FakeGc:
        def __init__(self):
            self.collect_calls = 0
            self.mode = "raise"

        def collect(self):
            self.collect_calls += 1

        def mem_free(self):
            if self.mode == "raise":
                raise RuntimeError("boom")
            return runtime_checks.TELNET_RECV_CHUNK_SIZE * 8

    fake_gc = FakeGc()

    monkeypatch.setattr(runtime_checks, "gc", fake_gc)
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: True)

    runtime_checks._maybe_collect_gc()
    assert fake_gc.collect_calls == 1

    fake_gc.mode = "enough"
    runtime_checks._maybe_collect_gc()
    assert fake_gc.collect_calls == 1


def test_telnet_session_helpers_cover_negotiated_and_fallback_failure_paths():
    negotiated_status, negotiated_detail = runtime_checks._classify_telnet_session(
        {
            "session_duration_ms": runtime_checks.TELNET_EARLY_CLOSE_THRESHOLD_MS,
            "close_reason": "idle-timeout",
            "handshake_detected": True,
            "has_visible_text": False,
            "visible_bytes": 0,
            "failure_detected": False,
        }
    )
    fallback_status, fallback_detail = runtime_checks._classify_telnet_session(
        {
            "session_duration_ms": 50.0,
            "close_reason": "refused",
            "handshake_detected": False,
            "has_visible_text": False,
            "visible_bytes": 0,
            "failure_detected": False,
        }
    )

    assert negotiated_status == Status.FAIL
    assert negotiated_detail == "no telnet response"
    assert fallback_status == Status.FAIL
    assert fallback_detail == "refused"
    assert runtime_checks._telnet_failure_detail({"close_reason": "idle-timeout", "session_duration_ms": 250.0}) == "no telnet response"


def test_telnet_failure_detail_covers_immediate_close_and_incomplete_response_paths():
    assert runtime_checks._telnet_failure_detail({"close_reason": "remote-close", "session_duration_ms": 10.0}) == "closed immediately"
    assert runtime_checks._telnet_failure_detail({"close_reason": "chunk-limit", "session_duration_ms": 500.0}) == "response not fully consumed"


def test_build_runtime_definitions_accepts_legacy_rest_alias_and_normalizes_to_http():
    definitions = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "legacy-api",
                    "name": "Legacy API",
                    "type": "REST",
                    "target": "https://example.invalid/health",
                }
            ]
        }
    )

    assert definitions[0].check_type == CheckType.HTTP


def test_build_runtime_definitions_reads_ftp_telnet_credentials_and_blank_auth():
    definitions = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "nas-ftp",
                    "name": "NAS FTP",
                    "type": "FTP",
                    "target": "ftp://nas.example.local",
                    "username": "admin",
                    "password": "secret",
                },
                {
                    "id": "switch-console",
                    "name": "Switch Console",
                    "type": "TELNET",
                    "target": "telnet://switch.example.local",
                    "username": "  ",
                    "password": "  ",
                },
            ]
        }
    )

    assert definitions[0].check_type == CheckType.FTP
    assert definitions[0].username == "admin"
    assert definitions[0].password == "secret"
    assert definitions[1].check_type == CheckType.TELNET
    assert definitions[1].username is None
    assert definitions[1].password is None


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


def test_build_executor_trace_sink_emits_probe_lifecycle_events():
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
    captured = []

    executor = build_executor(
        ping_runner=lambda target, timeout_s: PingProbeResult(ok=True, latency_ms=12.0, details="reachable"),
        trace_sink=lambda definition, event, fields: captured.append((definition.identifier, event, dict(fields))),
    )

    executor(definition, 10.0)

    assert captured[0] == ("router", "probe-start", {"timeout_s": 10, "target": "192.168.1.1"})
    assert captured[-1][0] == "router"
    assert captured[-1][1] == "probe-end"
    assert captured[-1][2]["status"] == "OK"
    assert captured[-1][2]["latency_ms"] == 12.0
    assert captured[-1][2]["probe_type"] == "PING"
    assert captured[-1][2]["issued"] == 1
    assert captured[-1][2]["succeeded"] == 1
    assert captured[-1][2]["failed"] == 0


def test_build_executor_tracks_probe_type_counters_across_issued_succeeded_and_failed_attempts(monkeypatch):
    definitions = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "router-a",
                    "name": "Router A",
                    "type": "PING",
                    "target": "192.168.1.1",
                    "interval_s": 15,
                    "timeout_s": 10,
                },
                {
                    "id": "router-b",
                    "name": "Router B",
                    "type": "PING",
                    "target": "192.168.1.2",
                    "interval_s": 15,
                    "timeout_s": 10,
                },
            ]
        }
    )
    captured = []
    results = iter(
        (
            PingProbeResult(ok=True, latency_ms=5.0, details="reachable"),
            PingProbeResult(ok=False, latency_ms=9.0, details="timeout"),
        )
    )

    def ping_runner(target, timeout_s):
        outcome = next(results)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    executor = build_executor(
        ping_runner=ping_runner,
        trace_sink=lambda definition, event, fields: captured.append((definition.identifier, event, dict(fields))),
    )

    executor(definitions[0], 10.0)
    executor(definitions[1], 11.0)

    monkeypatch.setattr(runtime_checks, "execute_check", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        executor(definitions[0], 12.0)

    assert captured[1] == (
        "router-a",
        "probe-end",
        {
            "status": "OK",
            "detail": "reachable",
            "latency_ms": 5.0,
            "observations": 1,
            "replace_source": False,
            "probe_type": "PING",
            "issued": 1,
            "succeeded": 1,
            "failed": 0,
            "target": "192.168.1.1",
        },
    )
    assert captured[3] == (
        "router-b",
        "probe-end",
        {
            "status": "FAIL",
            "detail": "timeout",
            "latency_ms": 9.0,
            "observations": 1,
            "replace_source": False,
            "probe_type": "PING",
            "issued": 2,
            "succeeded": 1,
            "failed": 1,
            "target": "192.168.1.2",
        },
    )
    assert captured[5] == (
        "router-a",
        "probe-error",
        {
            "detail": "boom",
            "probe_type": "PING",
            "issued": 3,
            "succeeded": 1,
            "failed": 2,
            "target": "192.168.1.1",
        },
    )


def test_build_executor_probe_end_adds_only_non_null_probe_metadata(monkeypatch):
    definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "telnet-a",
                    "name": "Telnet A",
                    "type": "TELNET",
                    "target": "telnet://192.168.1.2",
                    "interval_s": 15,
                    "timeout_s": 10,
                }
            ]
        }
    )[0]
    captured = []

    monkeypatch.setattr(
        runtime_checks,
        "execute_check",
        lambda *args, **kwargs: CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                CheckObservation(
                    identifier=definition.identifier,
                    name=definition.name,
                    status=Status.DEG,
                    details="connected-no-telnet-data",
                    latency_ms=12.0,
                    observed_at_s=10.0,
                ),
            ),
            probe_metadata={
                "close_reason": "idle-timeout",
                "session_duration_ms": None,
                "handshake_detected": False,
            },
        ),
    )

    executor = build_executor(trace_sink=lambda definition, event, fields: captured.append((definition.identifier, event, dict(fields))))
    executor(definition, 10.0)

    assert captured[-1] == (
        "telnet-a",
        "probe-end",
        {
            "status": "DEG",
            "detail": "connected-no-telnet-data",
            "latency_ms": 12.0,
            "observations": 1,
            "replace_source": False,
            "probe_type": "TELNET",
            "issued": 1,
            "succeeded": 0,
            "failed": 1,
            "target": "telnet://192.168.1.2",
            "close_reason": "idle-timeout",
            "handshake_detected": False,
        },
    )


def test_build_executor_probe_end_uses_probe_level_latency_for_service_checks():
    definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "service",
                    "name": "Service",
                    "type": "SERVICE",
                    "target": "http://service.local/checks",
                    "interval_s": 15,
                    "timeout_s": 10,
                    "service_prefix": "adb",
                }
            ]
        }
    )[0]
    captured = []

    executor = build_executor(
        http_runner=lambda method, target, timeout_s, username=None, password=None: runtime_checks.HttpResponseResult(
            status_code=200,
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
            latency_ms=7.5,
            details="HTTP 200",
        ),
        trace_sink=lambda definition, event, fields: captured.append((definition.identifier, event, dict(fields))),
    )

    executor(definition, 10.0)

    assert captured[-1][1] == "probe-end"
    assert captured[-1][2]["status"] == "OK"
    assert captured[-1][2]["latency_ms"] == 7.5
    assert captured[-1][2]["probe_type"] == "SERVICE"
    assert captured[-1][2]["issued"] == 1
    assert captured[-1][2]["succeeded"] == 1
    assert captured[-1][2]["failed"] == 0


def test_build_executor_uses_supplied_ftp_and_telnet_runners():
    ftp_definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "nas-ftp",
                    "name": "NAS FTP",
                    "type": "FTP",
                    "target": "ftp://nas.example.local",
                    "username": "admin",
                    "password": "secret",
                }
            ]
        }
    )[0]
    telnet_definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "switch-console",
                    "name": "Switch Console",
                    "type": "TELNET",
                    "target": "telnet://switch.example.local",
                }
            ]
        }
    )[0]
    calls = []
    executor = build_executor(
        ping_runner=lambda target, timeout_s: PingProbeResult(ok=True, latency_ms=1.0, details="reachable"),
        http_runner=None,
        ftp_runner=lambda target, timeout_s, username, password: (
            calls.append(("ftp", target, timeout_s, username, password))
            or PingProbeResult(ok=True, latency_ms=9.0, details="listed 1 entries")
        ),
        telnet_runner=lambda target, timeout_s, username, password: (
            calls.append(("telnet", target, timeout_s, username, password))
            or PingProbeResult(ok=True, latency_ms=4.0, details="session ready")
        ),
    )

    ftp_result = executor(ftp_definition, 10.0)
    telnet_result = executor(telnet_definition, 11.0)

    assert ftp_result.observations[0].status == Status.OK
    assert telnet_result.observations[0].status == Status.OK
    assert calls == [
        ("ftp", "ftp://nas.example.local", 10, "admin", "secret"),
        ("telnet", "telnet://switch.example.local", 10, None, None),
    ]


def test_portable_http_runner_prefers_urequests_when_available(monkeypatch):
    calls = []

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"checks": []}'

    class FakeConnection:
        def __init__(self, host, port, timeout):
            calls.append(("init", host, port, timeout))

        def request(self, method, path, headers=None):
            calls.append(("request", method, path, headers))

        def getresponse(self):
            return FakeResponse()

        def close(self):
            calls.append(("close",))

    monkeypatch.setitem(sys.modules, "http.client", SimpleNamespace(HTTPConnection=FakeConnection, HTTPSConnection=FakeConnection))

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10, password="ignored")

    assert result.status_code == 200
    assert result.body == {"checks": []}
    assert calls == [
        ("init", "192.0.2.10", 8080, 10),
        ("request", "GET", "/checks", {"Connection": "close"}),
        ("close",),
    ]


def test_portable_http_runner_reports_transient_transport_error_without_retry(monkeypatch):
    class BrokenConnection:
        def __init__(self, host, port, timeout):
            pass

        def request(self, method, path, headers=None):
            raise OSError("timed out")

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "http.client", SimpleNamespace(HTTPConnection=BrokenConnection, HTTPSConnection=BrokenConnection))

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.status_code is None
    assert result.details.startswith("timeout")


def test_portable_http_runner_falls_back_to_response_text_when_json_parsing_fails(monkeypatch):
    class FakeResponse:
        status = 200

        def read(self):
            return b"plain text"

    class FakeConnection:
        def __init__(self, host, port, timeout):
            pass

        def request(self, method, path, headers=None):
            return None

        def getresponse(self):
            return FakeResponse()

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "http.client", SimpleNamespace(HTTPConnection=FakeConnection, HTTPSConnection=FakeConnection))

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.body == "plain text"


def test_portable_http_runner_uses_urllib_fallback_for_success_and_http_error(monkeypatch):
    class FakeResponse:
        status = 503

        def read(self):
            return b"plain error"

    class FakeConnection:
        def __init__(self, host, port, timeout):
            pass

        def request(self, method, path, headers=None):
            return None

        def getresponse(self):
            return FakeResponse()

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "http.client", SimpleNamespace(HTTPConnection=FakeConnection, HTTPSConnection=FakeConnection))

    failure = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert failure.status_code == 503
    assert failure.body == "plain error"


def test_portable_http_runner_returns_classified_transport_error_after_retries(monkeypatch):
    class BrokenConnection:
        def __init__(self, host, port, timeout):
            pass

        def request(self, method, path, headers=None):
            raise OSError("network is unreachable")

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "http.client", SimpleNamespace(HTTPConnection=BrokenConnection, HTTPSConnection=BrokenConnection))

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.status_code is None
    assert result.details.startswith("network:")


def test_http_helpers_cover_import_fallback_and_invalid_payloads(monkeypatch):
    fallback_result = runtime_checks.HttpResponseResult(status_code=204, body="ok", latency_ms=1.0, details="HTTP 204")

    monkeypatch.setattr(runtime_checks.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError("missing")))
    monkeypatch.setattr(runtime_checks, "_portable_http_runner_socket", lambda method, target, timeout_s, trace=None: fallback_result)

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result is fallback_result
    assert runtime_checks._parse_http_target("http://example.local?view=1") == ("http", "example.local", 80, "/?view=1")

    with pytest.raises(ValueError, match="expected http target"):
        runtime_checks._parse_http_target("ftp://example.local")

    with pytest.raises(ValueError, match="target must include a host"):
        runtime_checks._parse_http_target("http://")

    with pytest.raises(ValueError, match="invalid HTTP response"):
        runtime_checks._parse_http_response(b"HTTP/1.1 200 OK\r\n")

    with pytest.raises(ValueError, match="invalid HTTP status"):
        runtime_checks._parse_http_response(b"HTTP/1.1 OK\r\n\r\nbody")


def test_import_module_falls_back_without_importlib(monkeypatch):
    http_client = SimpleNamespace(HTTPConnection=object, HTTPSConnection=object)
    monkeypatch.setitem(sys.modules, "http.client", http_client)
    monkeypatch.setattr(runtime_checks, "importlib", None)

    assert runtime_checks._import_module("http.client") is http_client


def test_deadline_and_error_helpers_cover_remaining_fallbacks(monkeypatch):
    monkeypatch.setattr(runtime_checks, "time", SimpleNamespace(perf_counter=lambda: 5.0))

    with pytest.raises(TimeoutError, match="timed out"):
        runtime_checks._deadline_remaining_s(("perf", 4.0))

    class BadAddress:
        def __getitem__(self, index):
            raise RuntimeError("bad address")

        def __str__(self):
            return "bad-address"

    assert runtime_checks._format_socket_address(BadAddress()) == "bad-address"
    assert runtime_checks._error_errno(OSError(115, "in progress")) == 115
    assert runtime_checks._is_already_connected(OSError("already connected")) is True
    assert runtime_checks._contains_any(b"Welcome READY", (b"ready",)) is True
    assert runtime_checks._classify_network_error(OSError(110, "ETIMEDOUT")) == "timeout"


def test_socket_wait_and_nonblocking_helpers_cover_timeout_and_fallback_paths(monkeypatch):
    timeout_events = []
    monkeypatch.setattr(runtime_checks, "_deadline_remaining_ms", lambda deadline: 0)

    with pytest.raises(TimeoutError, match="timed out"):
        runtime_checks._socket_wait(
            object(),
            ("perf", 0.0),
            writable=True,
            trace=lambda event, **fields: timeout_events.append((event, fields)),
            stage="connect",
        )

    assert timeout_events == [("socket-timeout", {"stage": "connect", "remain_ms": 0})]

    timeout_values = []
    monkeypatch.setattr(runtime_checks, "select", SimpleNamespace())
    monkeypatch.setattr(runtime_checks, "_deadline_remaining_ms", lambda deadline: 25)
    monkeypatch.setattr(runtime_checks, "_deadline_remaining_s", lambda deadline: 0.25)
    runtime_checks._socket_wait(
        SimpleNamespace(settimeout=lambda value: timeout_values.append(value)),
        ("perf", 1.0),
        writable=False,
        stage="recv",
    )
    assert timeout_values == [0.25]

    register_values = []

    class BrokenPoll:
        def register(self, handle, flags):
            raise RuntimeError("register failed")

    monkeypatch.setattr(runtime_checks, "select", SimpleNamespace(poll=lambda: BrokenPoll()))
    runtime_checks._socket_wait(
        SimpleNamespace(settimeout=lambda value: register_values.append(value)),
        ("perf", 1.0),
        writable=False,
        stage="recv",
    )
    assert register_values == [0.25]

    poll_timeout_events = []

    class EmptyPoll:
        def register(self, handle, flags):
            return None

        def poll(self, timeout):
            return []

    monkeypatch.setattr(runtime_checks, "select", SimpleNamespace(poll=lambda: EmptyPoll()))

    with pytest.raises(TimeoutError, match="timed out"):
        runtime_checks._socket_wait(
            object(),
            ("perf", 1.0),
            writable=True,
            trace=lambda event, **fields: poll_timeout_events.append((event, fields)),
            stage="send",
        )

    assert poll_timeout_events == [("socket-timeout", {"stage": "send", "remain_ms": 0})]

    class TimeoutOnlySocket:
        def __init__(self):
            self.values = []

        def settimeout(self, value):
            self.values.append(value)

    timeout_only = TimeoutOnlySocket()
    assert runtime_checks._set_nonblocking_socket(timeout_only, True) is True
    assert timeout_only.values == [0]

    class BrokenBlockingSocket:
        def setblocking(self, enabled):
            raise RuntimeError("boom")

    assert runtime_checks._set_nonblocking_socket(BrokenBlockingSocket(), True) is False
    assert runtime_checks._set_nonblocking_socket(object(), False) is False


def test_connect_and_socket_compat_helpers_cover_remaining_branches(monkeypatch):
    class DirectSocket:
        def __init__(self):
            self.connected = []
            self.timeouts = []

        def connect(self, address):
            self.connected.append(address)

        def settimeout(self, value):
            self.timeouts.append(value)

    direct_handle = DirectSocket()
    monkeypatch.setattr(runtime_checks, "_set_nonblocking_socket", lambda handle, enabled: False)
    monkeypatch.setattr(runtime_checks, "_deadline_remaining_s", lambda deadline: 0.5)

    runtime_checks._connect_socket(direct_handle, ("nas.example.local", 21), 10, ("perf", 10.0))

    assert direct_handle.connected == [("nas.example.local", 21)]
    assert direct_handle.timeouts == [0.5, 10]

    class AsyncSocket:
        def __init__(self, *, sock_error=0, second_error=None):
            self.connect_calls = 0
            self.sock_error = sock_error
            self.second_error = second_error
            self.timeouts = []

        def connect(self, address):
            self.connect_calls += 1
            if self.connect_calls == 1:
                raise OSError(115, "operation in progress")
            if self.second_error is not None:
                raise self.second_error

        def settimeout(self, value):
            self.timeouts.append(value)

        def getsockopt(self, level, option):
            return self.sock_error

    monkeypatch.setattr(runtime_checks, "_set_nonblocking_socket", lambda handle, enabled: True)
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="connect": None)

    already_connected = AsyncSocket(second_error=OSError("already connected"))
    runtime_checks._connect_socket(already_connected, ("nas.example.local", 21), 10, ("perf", 10.0))
    assert already_connected.timeouts == [10]

    with pytest.raises(OSError, match="connect failed"):
        runtime_checks._connect_socket(AsyncSocket(sock_error=111), ("nas.example.local", 21), 10, ("perf", 10.0))

    with pytest.raises(OSError, match="boom"):
        runtime_checks._connect_socket(AsyncSocket(second_error=OSError("boom")), ("nas.example.local", 21), 10, ("perf", 10.0))

    retrying = AsyncSocket()

    def retrying_connect(address):
        retrying.connect_calls += 1
        if retrying.connect_calls < 3:
            raise OSError(115, "operation in progress")

    retrying.connect = retrying_connect
    runtime_checks._connect_socket(retrying, ("nas.example.local", 21), 10, ("perf", 10.0))
    assert retrying.timeouts == [10]

    monkeypatch.setattr(runtime_checks, "_open_socket", lambda host, port, timeout_s, **kwargs: (_ for _ in ()).throw(TypeError("other")))
    with pytest.raises(TypeError, match="other"):
        runtime_checks._open_socket_compat("nas.example.local", 21, 10, ("perf", 1.0))

    call_log = []
    trace_marker = object()

    def compat_open_socket(host, port, timeout_s, **kwargs):
        call_log.append(kwargs)
        if kwargs:
            raise TypeError("deadline unsupported")
        return "opened"

    monkeypatch.setattr(runtime_checks, "_open_socket", compat_open_socket)
    assert runtime_checks._open_socket_compat("nas.example.local", 21, 10, ("perf", 1.0), trace=trace_marker) == "opened"
    assert call_log == [{"deadline": ("perf", 1.0), "trace": trace_marker}, {}]


def test_recv_and_send_socket_helpers_cover_remaining_branches(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="send": None)

    runtime_checks._socket_sendall(object(), b"", ("perf", 1.0))

    class SenderSocket:
        def __init__(self, responses):
            self.responses = list(responses)
            self.sent_chunks = []

        def send(self, payload):
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            self.sent_chunks.append(bytes(payload[: response or 0]))
            return response

    trace_events = []
    sender = SenderSocket([OSError("would block"), 2, 3])
    runtime_checks._socket_sendall(
        sender,
        b"hello",
        ("perf", 1.0),
        trace=lambda event, **fields: trace_events.append((event, fields)),
        stage="send",
    )
    assert sender.sent_chunks == [b"he", b"llo"]
    assert trace_events == [
        ("socket-send", {"stage": "send", "bytes_sent": 2}),
        ("socket-send", {"stage": "send", "bytes_sent": 3}),
    ]

    class NoneSenderSocket:
        def send(self, payload):
            return None

    runtime_checks._socket_sendall(NoneSenderSocket(), b"ok", ("perf", 1.0))

    class ZeroSenderSocket:
        def send(self, payload):
            return 0

    with pytest.raises(OSError, match="send failed"):
        runtime_checks._socket_sendall(ZeroSenderSocket(), b"ok", ("perf", 1.0))

    class SendallSocket:
        def __init__(self):
            self.calls = 0

        def sendall(self, payload):
            self.calls += 1
            if self.calls == 1:
                raise OSError("would block")
            return None

    fallback_trace = []
    runtime_checks._socket_sendall(
        SendallSocket(),
        b"payload",
        ("perf", 1.0),
        trace=lambda event, **fields: fallback_trace.append((event, fields)),
        stage="sendall",
    )
    assert fallback_trace == [("socket-send", {"stage": "sendall", "bytes_sent": 7})]

    recv_trace = []

    class RecvSocket:
        def __init__(self):
            self.calls = 0

        def recv(self, size):
            self.calls += 1
            if self.calls == 1:
                raise OSError("would block")
            if self.calls == 2:
                raise OSError("timed out")
            return b"ok"

    with pytest.raises(TimeoutError, match="timed out"):
        runtime_checks._socket_recv(
            RecvSocket(),
            4096,
            ("perf", 1.0),
            trace=lambda event, **fields: recv_trace.append((event, fields)),
            stage="recv",
        )

    assert recv_trace[-1] == ("socket-timeout", {"stage": "recv", "remain_ms": 0})

    class GoodRecvSocket:
        def recv(self, size):
            return b"done"

    success_trace = []
    assert runtime_checks._socket_recv(
        GoodRecvSocket(),
        4096,
        ("perf", 1.0),
        trace=lambda event, **fields: success_trace.append((event, fields)),
        stage="recv",
    ) == b"done"
    assert success_trace == [("socket-recv", {"stage": "recv", "bytes_received": 4})]

    handle = FakeSocket([])
    runtime_checks._ftp_command(handle, "NOOP")
    assert handle.sent == [b"NOOP\r\n"]


def test_telnet_chunk_compat_and_output_helpers_cover_remaining_branches(monkeypatch):
    original_recv_telnet_chunk = runtime_checks._recv_telnet_chunk

    monkeypatch.setattr(runtime_checks, "_recv_telnet_chunk", lambda handle, size, deadline=None, trace=None, budget=None: (_ for _ in ()).throw(TypeError("other")))
    with pytest.raises(TypeError, match="other"):
        runtime_checks._recv_telnet_chunk_compat(object(), 4096, deadline=("perf", 1.0), trace=object())

    calls = []

    def timeout_compat(handle, size, deadline=None, trace=None, budget=None):
        calls.append((deadline, trace))
        if deadline is not None or trace is not None or budget is not None:
            raise TypeError("deadline unsupported")
        raise OSError("timed out")

    monkeypatch.setattr(runtime_checks, "_recv_telnet_chunk", timeout_compat)
    assert runtime_checks._recv_telnet_chunk_compat(object(), 4096, deadline=("perf", 1.0), trace=object()) == b""
    assert len(calls) == 2

    def broken_compat(handle, size, deadline=None, trace=None, budget=None):
        if deadline is not None or trace is not None or budget is not None:
            raise TypeError("trace unsupported")
        raise OSError("broken pipe")

    monkeypatch.setattr(runtime_checks, "_recv_telnet_chunk", broken_compat)
    with pytest.raises(OSError, match="broken pipe"):
        runtime_checks._recv_telnet_chunk_compat(object(), 4096, deadline=("perf", 1.0), trace=object())

    class DeadlineRecvSocket:
        pass

    monkeypatch.setattr(runtime_checks, "_recv_telnet_chunk", original_recv_telnet_chunk)
    monkeypatch.setattr(runtime_checks, "_socket_recv", lambda handle, size, deadline, trace=None, stage="telnet-recv", budget=None: b"READY")
    assert runtime_checks._recv_telnet_chunk(DeadlineRecvSocket(), deadline=("perf", 1.0)) == b"READY"

    monkeypatch.setattr(runtime_checks, "_socket_recv", lambda handle, size, deadline, trace=None, stage="telnet-recv", budget=None: (_ for _ in ()).throw(TimeoutError("timed out")))
    assert runtime_checks._recv_telnet_chunk(DeadlineRecvSocket(), deadline=("perf", 1.0)) == b""

    monkeypatch.setattr(runtime_checks, "_socket_recv", lambda handle, size, deadline, trace=None, stage="telnet-recv", budget=None: (_ for _ in ()).throw(TimeoutError("probe io budget exhausted")))
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._recv_telnet_chunk(DeadlineRecvSocket(), deadline=("perf", 1.0))

    assert runtime_checks._looks_like_telnet_output("   ") is False


def test_recv_telnet_chunk_into_traces_recv_into_and_recv_fallback_paths():
    trace_events = []

    class IntoSocket:
        def recv_into(self, buffer):
            payload = b"abc"
            buffer[: len(payload)] = payload
            return len(payload)

    into_result = runtime_checks._recv_telnet_chunk_into(
        IntoSocket(),
        bytearray(8),
        trace=lambda event, **fields: trace_events.append((event, fields)),
    )

    assert bytes(into_result) == b"abc"
    assert trace_events == [("socket-recv", {"stage": "telnet-recv", "operation": "read-visible", "bytes_received": 3})]

    trace_events.clear()

    class FallbackSocket:
        def recv(self, size):
            assert size == 8
            return b"xy"

    fallback_result = runtime_checks._recv_telnet_chunk_into(
        FallbackSocket(),
        bytearray(8),
        trace=lambda event, **fields: trace_events.append((event, fields)),
    )

    assert fallback_result == b"xy"
    assert trace_events == [("socket-recv", {"stage": "telnet-recv", "operation": "read-visible", "bytes_received": 2})]


def test_recv_telnet_chunk_retries_without_target_when_socket_recv_has_legacy_signature(monkeypatch):
    calls = []

    def fake_socket_recv(handle, size, deadline, trace=None, stage="telnet-recv", operation=None, target=None, budget=None):
        calls.append((operation, target, budget))
        if operation is not None or target is not None:
            raise TypeError("target unsupported")
        return b"READY"

    monkeypatch.setattr(runtime_checks, "_socket_recv", fake_socket_recv)

    assert runtime_checks._recv_telnet_chunk(object(), deadline=("perf", 1.0), target="switch:23", budget="budget-token") == b"READY"
    assert calls == [
        (None, "switch:23", "budget-token"),
        (None, None, "budget-token"),
    ]


def test_portable_telnet_runner_treats_micropython_etimedout_after_connect_as_degraded(monkeypatch):
    class TimeoutSocket:
        def __init__(self):
            self.timeout_values = []
            self.closed = False

        def settimeout(self, value):
            self.timeout_values.append(value)

        def recv(self, size):
            raise OSError(110, "ETIMEDOUT")

        def close(self):
            self.closed = True

    handle = TimeoutSocket()

    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: True)
    monkeypatch.setattr(runtime_checks, "_open_socket_compat", lambda host, port, timeout_s, deadline, trace=None: handle)

    result = portable_telnet_runner("192.0.2.10:23", 8)

    assert result.ok is False
    assert result.status == Status.FAIL
    assert result.details == "no telnet response"
    assert handle.timeout_values == [runtime_checks.TELNET_IDLE_TIMEOUT_S] * 5
    assert handle.closed is True


def test_portable_ftp_runner_stdlib_paths_cover_success_and_failures(monkeypatch):
    class FakeFTP:
        def __init__(self, *, greeting="220 Ready", login="230 Logged in", pwd="/", goodbye="221 Bye", close_error=None):
            self.greeting = greeting
            self.login_response = login
            self.pwd_response = pwd
            self.goodbye = goodbye
            self.close_error = close_error
            self.calls = []

        def connect(self, host, port, timeout):
            self.calls.append(("connect", host, port, timeout))
            return self.greeting

        def login(self, username, password):
            self.calls.append(("login", username, password))
            return self.login_response

        def pwd(self):
            self.calls.append(("pwd",))
            return self.pwd_response

        def quit(self):
            self.calls.append(("quit",))
            return self.goodbye

        def close(self):
            self.calls.append(("close",))
            if self.close_error is not None:
                raise self.close_error

    ftp_success = FakeFTP(close_error=OSError("close failed"))
    monkeypatch.setitem(sys.modules, "ftplib", SimpleNamespace(FTP=lambda: ftp_success))

    success = portable_ftp_runner("ftp://nas.example.local", 10, username="admin", password="secret")

    assert success.ok is True
    assert success.details == "pwd=/"
    assert ftp_success.calls[:4] == [
        ("connect", "nas.example.local", 21, 10),
        ("login", "admin", "secret"),
        ("pwd",),
        ("quit",),
    ]

    monkeypatch.setitem(sys.modules, "ftplib", SimpleNamespace(FTP=lambda: FakeFTP(pwd="")))
    empty = portable_ftp_runner("ftp://nas.example.local", 10)
    assert empty.ok is False
    assert empty.details == "empty FTP PWD response"

    monkeypatch.setitem(sys.modules, "ftplib", SimpleNamespace(FTP=lambda: FakeFTP(goodbye="500 Bad quit")))
    bad_quit = portable_ftp_runner("ftp://nas.example.local", 10)
    assert bad_quit.ok is False
    assert bad_quit.details == "expected FTP 221, got 500 Bad quit"

    monkeypatch.setitem(sys.modules, "ftplib", SimpleNamespace(FTP=lambda: FakeFTP(greeting="500 Down")))
    bad_greeting = portable_ftp_runner("ftp://nas.example.local", 10)
    assert bad_greeting.ok is False
    assert bad_greeting.details == "expected FTP 220, got 500 Down"

    monkeypatch.setitem(sys.modules, "ftplib", SimpleNamespace(FTP=lambda: FakeFTP(login="530 Not logged in")))
    bad_login = portable_ftp_runner("ftp://nas.example.local", 10)
    assert bad_login.ok is False
    assert bad_login.details == "expected FTP 230, got 530 Not logged in"


def test_portable_ftp_runner_falls_back_to_raw_path_when_ftplib_is_missing(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ftplib":
            raise ImportError("no ftplib")
        return original_import(name, globals, locals, fromlist, level)

    control_socket = FakeSocket(
        [
            b"220 Ready\r\n",
            b"331 Password required\r\n",
            b"230 Logged in\r\n",
            b'257 "/" is current directory\r\n',
            b"221 Bye\r\n",
        ]
    )

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(runtime_checks, "_open_socket_compat", lambda *args, **kwargs: control_socket)
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: False)

    result = portable_ftp_runner("ftp://nas.example.local", 10)

    assert result.ok is True
    assert result.details == "pwd=/"


def test_portable_ftp_runner_raw_path_reports_remaining_failures(monkeypatch):
    def run_case(control_responses):
        control_socket = FakeSocket(control_responses)

        def fake_open_socket(host, port, timeout_s, **kwargs):
            return control_socket

        monkeypatch.setattr(runtime_checks, "_open_socket", fake_open_socket)
        return portable_ftp_runner("ftp://nas.example.local", 10, trace=lambda event, **fields: None)

    assert run_case([
        b"220 Ready\r\n",
        b"230 Logged in\r\n",
        b"500 No pwd\r\n",
    ]).details == "expected FTP 257, got 500 No pwd"
    assert run_case([
        b"220 Ready\r\n",
        b"230 Logged in\r\n",
        b"257 /\r\n",
        b"500 Bad quit\r\n",
    ]).details == "expected FTP 221, got 500 Bad quit"


def test_ftp_parse_pwd_rejects_invalid_responses():
    assert runtime_checks._ftp_parse_pwd("257 /Temp") == "/Temp"
    assert runtime_checks._ftp_parse_pwd('257 ""') == '""'

    with pytest.raises(ValueError, match="invalid FTP PWD response"):
        runtime_checks._ftp_parse_pwd("250 not a pwd response")

    with pytest.raises(ValueError, match="invalid FTP PWD response"):
        runtime_checks._ftp_parse_pwd("257   ")


def test_portable_ftp_runner_raw_path_skips_pass_when_user_is_already_logged_in(monkeypatch):
    control_socket = FakeSocket(
        [
            b"220 Ready\r\n",
            b"230 Logged in\r\n",
            b"257 /Temp\r\n",
            b"221 Bye\r\n",
        ]
    )

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: control_socket)

    result = portable_ftp_runner("ftp://nas.example.local", 10, trace=lambda event, **fields: None)

    assert result.ok is True
    assert result.details == "pwd=/Temp"
    assert control_socket.sent == [
        b"USER anonymous\r\n",
        b"PWD\r\n",
        b"QUIT\r\n",
    ]


def test_portable_telnet_runner_stdlib_and_raw_error_paths(monkeypatch):
    class StdlibSocket:
        def __init__(self, responses):
            self.responses = list(responses)
            self.sent = []
            self.timeout = None
            self.closed = False

        def settimeout(self, value):
            self.timeout = value

        def sendall(self, data):
            self.sent.append(data)

        def recv(self, size):
            if not self.responses:
                return b""
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        def close(self):
            self.closed = True

    success_handle = StdlibSocket(
        [
            b'\xff\xfe"\xff\xfb\x01\x1bc\x1b[0;37;2m\x1b[1;1H\x1bc\x1b[0;37;2m\x1b[1;1H\x1b[1;6H\x1b[0;37;1m*** C64 Ultimate (V1.49) 1.1.0 *** Remote ***\x1b[24;53H\x1b(BF7=HELP',
            runtime_checks.socket.timeout(),
            runtime_checks.socket.timeout(),
            runtime_checks.socket.timeout(),
            runtime_checks.socket.timeout(),
            runtime_checks.socket.timeout(),
        ]
    )
    monkeypatch.setattr(runtime_checks.socket, "create_connection", lambda address, timeout: success_handle)
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: False)

    success = portable_telnet_runner("telnet://switch.example.local", 10)
    assert success.ok is True
    assert success.status == Status.OK
    assert success.details == "response-received"
    assert success_handle.sent == [bytes((255, 252, 34)), bytes((255, 254, 1))]
    assert success_handle.closed is True

    empty_handle = StdlibSocket([runtime_checks.socket.timeout(), b""])
    monkeypatch.setattr(runtime_checks.socket, "create_connection", lambda address, timeout: empty_handle)

    empty = portable_telnet_runner("telnet://switch.example.local", 10)
    assert empty.ok is False
    assert empty.status == Status.FAIL
    assert empty.details == "no telnet response"

    class TimeoutSocket(FakeSocket):
        def __init__(self):
            super().__init__([])
            self.calls = 0

        def recv(self, size):
            self.calls += 1
            raise TimeoutError("timed out")

    timeout_handle = TimeoutSocket()
    monkeypatch.setattr(runtime_checks, "_open_socket", lambda host, port, timeout_s: timeout_handle)
    timeout_result = portable_telnet_runner("telnet://switch.example.local", 10, trace=lambda event, **fields: None)
    assert timeout_result.ok is False
    assert timeout_result.status == Status.FAIL
    assert timeout_result.details == "no telnet response"
    assert timeout_handle.calls == 5

    class BrokenSocket(FakeSocket):
        def recv(self, size):
            raise OSError("broken pipe")

    broken_handle = BrokenSocket([])
    monkeypatch.setattr(runtime_checks, "_open_socket", lambda host, port, timeout_s: broken_handle)
    broken_result = portable_telnet_runner("telnet://switch.example.local", 10, trace=lambda event, **fields: None)
    assert broken_result.ok is False
    assert broken_result.status == Status.FAIL
    assert broken_result.details == "closed immediately"


def test_portable_telnet_runner_stdlib_rejects_immediate_post_connect_reset(monkeypatch):
    class ResetSocket:
        def __init__(self):
            self.closed = False

        def settimeout(self, value):
            return None

        def recv(self, size):
            raise OSError("broken pipe")

        def close(self):
            self.closed = True

    handle = ResetSocket()
    monkeypatch.setattr(runtime_checks.socket, "create_connection", lambda address, timeout: handle)
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: False)

    result = portable_telnet_runner("telnet://switch.example.local", 10)

    assert result.ok is False
    assert result.status == Status.FAIL
    assert result.details == "closed immediately"
    assert handle.closed is True


def test_portable_telnet_runner_stdlib_reports_connection_failure_metadata(monkeypatch):
    monkeypatch.setattr(runtime_checks.socket, "create_connection", lambda address, timeout: (_ for _ in ()).throw(OSError("refused")))
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: False)

    result = portable_telnet_runner("telnet://switch.example.local", 10)

    assert result.ok is False
    assert result.status == Status.FAIL
    assert result.details == "refused"
    assert result.metadata == {
        "close_reason": "refused",
        "session_duration_ms": 0.0,
        "handshake_detected": False,
        "response_received": False,
    }


def test_portable_telnet_runner_stdlib_rejects_failure_marker(monkeypatch):
    class FailureSocket:
        def __init__(self):
            self.responses = [b"Access denied\r\n", b""]
            self.closed = False

        def settimeout(self, value):
            return None

        def recv(self, size):
            return self.responses.pop(0)

        def close(self):
            self.closed = True

    handle = FailureSocket()
    monkeypatch.setattr(runtime_checks.socket, "create_connection", lambda address, timeout: handle)
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: False)

    result = portable_telnet_runner("telnet://switch.example.local", 10)

    assert result.ok is False
    assert result.details == "telnet failure marker present"
    assert handle.closed is True


def test_telnet_send_best_effort_reraises_non_timeout_errors():
    class BrokenHandle:
        def sendall(self, payload):
            raise OSError("broken pipe")

    with pytest.raises(OSError, match="broken pipe"):
        runtime_checks._telnet_send_best_effort(BrokenHandle(), b"hello")


def test_manual_http_socket_and_executor_defaults_cover_remaining_paths(monkeypatch):
    with pytest.raises(OSError, match="https unsupported on device"):
        runtime_checks._portable_http_runner_socket("GET", "https://example.local/health", 10)

    monkeypatch.setattr(runtime_checks, "_open_socket_compat", lambda host, port, timeout_s, deadline, trace=None: (_ for _ in ()).throw(OSError("refused")))
    failure = runtime_checks._portable_http_runner_socket("GET", "http://example.local/health", 10)
    assert failure.status_code is None
    assert failure.details == "refused"

    calls = []
    monkeypatch.setattr(runtime_checks, "portable_ping_runner", lambda target, timeout_s: calls.append(("ping", target, timeout_s)) or PingProbeResult(ok=True, latency_ms=1.0, details="reachable"))
    monkeypatch.setattr(runtime_checks, "portable_http_runner", lambda method, target, timeout_s, username=None, password=None, trace=None: calls.append(("http", method, target, timeout_s, username, password, trace)) or runtime_checks.HttpResponseResult(status_code=200, body={}, latency_ms=2.0, details="HTTP 200"))
    monkeypatch.setattr(runtime_checks, "portable_ftp_runner", lambda target, timeout_s, username=None, password=None, trace=None: calls.append(("ftp", target, timeout_s, username, password, trace)) or PingProbeResult(ok=True, latency_ms=3.0, details="listed"))
    monkeypatch.setattr(runtime_checks, "portable_telnet_runner", lambda target, timeout_s, username=None, password=None, trace=None: calls.append(("telnet", target, timeout_s, username, password, trace)) or PingProbeResult(ok=True, latency_ms=4.0, details="banner"))

    executor = build_executor()
    definitions = build_runtime_definitions(
        {
            "checks": [
                {"id": "ping", "name": "Ping", "type": "PING", "target": "device.local"},
                {"id": "http", "name": "HTTP", "type": "HTTP", "target": "http://device.local/health", "username": "ops", "password": "secret"},
                {"id": "ftp", "name": "FTP", "type": "FTP", "target": "ftp://device.local", "username": "ops", "password": "secret"},
                {"id": "telnet", "name": "TELNET", "type": "TELNET", "target": "telnet://device.local"},
            ]
        }
    )

    for definition in definitions:
        result = executor(definition, 10.0)
        assert result.observations[0].status == Status.OK

    assert [entry[0] for entry in calls] == ["ping", "http", "ftp", "telnet"]
    assert calls[1][4:6] == ("ops", "secret")
    assert calls[2][3:5] == ("ops", "secret")

    http_calls = []
    custom_executor = build_executor(
        http_runner=lambda method, target, timeout_s, username=None, password=None: http_calls.append((method, target, timeout_s, username, password)) or runtime_checks.HttpResponseResult(status_code=200, body={}, latency_ms=1.0, details="HTTP 200")
    )
    custom_definitions = build_runtime_definitions(
        {
            "checks": [
                {"id": "with-auth", "name": "With Auth", "type": "HTTP", "target": "http://device.local/auth", "username": "ops", "password": "secret"},
                {"id": "no-auth", "name": "No Auth", "type": "HTTP", "target": "http://device.local/public"},
            ]
        }
    )
    for definition in custom_definitions:
        custom_executor(definition, 10.0)
    assert http_calls == [
        ("GET", "http://device.local/auth", 10, "ops", "secret"),
        ("GET", "http://device.local/public", 10, None, None),
    ]

    trace_events = []
    failing_executor = build_executor(trace_sink=lambda definition, event, fields: trace_events.append((definition.identifier, event, fields)))
    monkeypatch.setattr(runtime_checks, "execute_check", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        failing_executor(definitions[0], 10.0)

    assert trace_events == [
        ("ping", "probe-start", {"timeout_s": 10, "target": "device.local"}),
        ("ping", "probe-error", {"detail": "boom", "probe_type": "PING", "issued": 1, "succeeded": 0, "failed": 1, "target": "device.local"}),
    ]


def test_build_executor_preserves_explicit_trace_target(monkeypatch):
    definition = build_runtime_definitions(
        {
            "checks": [
                {
                    "id": "http",
                    "name": "HTTP",
                    "type": "HTTP",
                    "target": "http://device.local/health",
                    "timeout_s": 10,
                }
            ]
        }
    )[0]
    captured = []

    def portable_http_runner(method, target, timeout_s, username=None, password=None, trace=None):
        del method, username, password
        trace("socket-open", target="override.local")
        return runtime_checks.HttpResponseResult(status_code=200, body={}, latency_ms=1.0, details="HTTP 200")

    monkeypatch.setattr(runtime_checks, "portable_http_runner", portable_http_runner)

    monkeypatch.setattr(
        runtime_checks,
        "execute_check",
        lambda definition, now_s, ping, http, ftp, telnet: (
            http("GET", definition.target, definition.timeout_s),
            CheckExecutionResult(
                source_identifier=definition.identifier,
                observations=(
                    CheckObservation(
                        identifier=definition.identifier,
                        name=definition.name,
                        status=Status.OK,
                        details="HTTP 200",
                        latency_ms=1.0,
                        observed_at_s=now_s,
                    ),
                ),
            ),
        )[1],
    )

    executor = build_executor(trace_sink=lambda definition, event, fields: captured.append((event, dict(fields))))
    executor(definition, 10.0)

    assert any(event == "socket-open" and fields == {"target": "override.local"} for event, fields in captured)


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


def test_portable_ping_runner_reports_timeout_without_retry(monkeypatch):
    monkeypatch.delitem(sys.modules, "uping", raising=False)

    import subprocess

    sleep_calls = []
    attempts = {"count": 0}

    def fake_run(*args, **kwargs):
        attempts["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="timeout")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("vivipi.runtime.checks._sleep_ms", lambda value_ms: sleep_calls.append(value_ms))

    result = portable_ping_runner("192.168.1.1", 10)

    assert result.ok is False
    assert result.details == "timeout"
    assert attempts["count"] == 1
    assert sleep_calls == []


def test_portable_ping_runner_reports_unsupported_without_uping_or_subprocess(monkeypatch):
    monkeypatch.delitem(sys.modules, "uping", raising=False)

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "subprocess":
            raise ImportError("no subprocess")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = portable_ping_runner("192.168.1.1", 10)

    assert result.ok is False
    assert result.details == "ICMP unsupported on device"


def test_network_helper_functions_cover_edge_cases(monkeypatch):
    fake_time = SimpleNamespace(
        now_ms=100,
        perf_value=1.5,
        sleep_calls=[],
        sleep_ms=lambda value_ms: fake_time.sleep_calls.append(value_ms),
        sleep=lambda value_s: fake_time.sleep_calls.append(value_s),
        ticks_ms=lambda: fake_time.now_ms,
        ticks_diff=lambda current, started: current - started,
        perf_counter=lambda: fake_time.perf_value,
    )
    monkeypatch.setattr(runtime_checks, "time", fake_time)

    _sleep_ms(0)
    _sleep_ms(5)

    started_at, uses_ticks_ms = runtime_checks._start_timer()
    fake_time.now_ms = 140

    assert uses_ticks_ms is True
    assert runtime_checks._elapsed_ms(started_at, uses_ticks_ms) == 40.0

    assert fake_time.sleep_calls == [5]
    assert _normalize_error_text(RuntimeError()) == "RuntimeError"
    assert _classify_network_error(TimeoutError("timed out")) == "timeout"
    assert _classify_network_error(OSError(-2, "name resolution failed")) == "dns"
    assert _classify_network_error(OSError(111, "connection refused")) == "refused"
    assert _classify_network_error(OSError(101, "network unreachable")) == "network"
    assert _classify_network_error(OSError("broken pipe")) == "reset"
    assert _format_network_error(OSError("broken pipe")).startswith("reset:")
    assert runtime_checks._error_errno(Exception()) is None
    assert runtime_checks._error_errno(Exception(115, "in progress")) == 115
    assert _runtime_optional_auth(None) is None
    assert _runtime_optional_auth("  admin  ") == "admin"
    assert _parse_socket_target("nas.example.local", 21) == ("nas.example.local", 21)
    assert _recv_all(FakeSocket([b"a", b"b", b""])) == b"ab"

    monkeypatch.setattr(runtime_checks, "time", SimpleNamespace(perf_counter=lambda: 2.5, sleep=lambda value_s: None))

    perf_started_at, uses_ticks_ms = runtime_checks._start_timer()

    assert uses_ticks_ms is False
    assert runtime_checks._elapsed_ms(perf_started_at, uses_ticks_ms) == 0.0


def test_runtime_target_alias_resolution_rewrites_matching_hosts_only():
    assert runtime_checks._resolve_target_alias("router.local", {"router.local": "192.0.2.10"}) == "192.0.2.10"
    assert runtime_checks._resolve_target_alias(
        "http://router.local/health",
        {"router.local": "192.0.2.10"},
    ) == "http://192.0.2.10/health"
    assert runtime_checks._resolve_target_alias(
        "http://router.local/health",
        {"other.local": "192.0.2.20"},
    ) == "http://router.local/health"
    assert runtime_checks._resolve_target_alias("router.local", {"router.local": "   "}) == "router.local"
    assert runtime_checks._resolve_target_alias("router.local", {"other.local": "192.0.2.20"}) == "router.local"


def test_network_helper_functions_cover_additional_fallback_paths(monkeypatch):
    fake_time = SimpleNamespace(sleep_calls=[], sleep=lambda value_s: fake_time.sleep_calls.append(value_s))
    monkeypatch.setattr(runtime_checks, "time", fake_time)

    runtime_checks._sleep_ms(5)

    assert fake_time.sleep_calls == [0.005]
    assert _classify_network_error(OSError("weird failure")) == "io"
    assert runtime_checks._resolve_target_alias("", {"router.local": "192.0.2.10"}) == ""
    assert runtime_checks._resolve_target_alias("http://router.local/health", {"router.local": "   "}) == "http://router.local/health"
    assert runtime_checks._error_errno(OSError("boom", 115)) is None


def test_error_errno_uses_integer_first_arg_and_socket_helpers_reraise_other_errors(monkeypatch):
    assert runtime_checks._error_errno(OSError(115, "in progress")) == 115

    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="send": None)

    class SenderSocket:
        def send(self, payload):
            raise OSError("send boom")

    with pytest.raises(OSError, match="send boom"):
        runtime_checks._socket_sendall(SenderSocket(), b"payload", ("perf", 1.0))

    class SendallSocket:
        def sendall(self, payload):
            raise OSError("sendall boom")

    with pytest.raises(OSError, match="sendall boom"):
        runtime_checks._socket_sendall(SendallSocket(), b"payload", ("perf", 1.0))

    class RecvSocket:
        def recv(self, size):
            raise OSError("recv boom")

    with pytest.raises(OSError, match="recv boom"):
        runtime_checks._socket_recv(RecvSocket(), 32, ("perf", 1.0))


def test_open_socket_reports_dns_errors(monkeypatch):
    trace_events = []
    monkeypatch.setattr(runtime_checks.socket, "getaddrinfo", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("name resolution failed")))

    with pytest.raises(OSError, match="name resolution failed"):
        _open_socket(
            "nas.example.local",
            21,
            10,
            trace=lambda event, **fields: trace_events.append((event, fields)),
        )

    assert trace_events[0] == ("dns-start", {"host": "nas.example.local", "port": 21, "target": "nas.example.local:21"})
    assert trace_events[1][0] == "dns-error"


def test_portable_http_runner_urllib_fallback_covers_plaintext_and_transport_errors(monkeypatch):
    class FakePlainResponse:
        status = 200

        def read(self):
            return b"plain text"

    class FakeConnection:
        def __init__(self, host, port, timeout):
            self.mode = "success"

        def request(self, method, path, headers=None):
            return None

        def getresponse(self):
            return FakePlainResponse()

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "http.client", SimpleNamespace(HTTPConnection=FakeConnection, HTTPSConnection=FakeConnection))
    success = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert success.body == "plain text"

    class BrokenConnection:
        def __init__(self, host, port, timeout):
            return None

        def request(self, method, path, headers=None):
            raise OSError("network down")

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "http.client", SimpleNamespace(HTTPConnection=BrokenConnection, HTTPSConnection=BrokenConnection))
    failure = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert failure.status_code is None
    assert failure.details == "io: network down"


def test_socket_and_telnet_helpers_cover_remaining_edge_cases(monkeypatch):
    monkeypatch.setattr(runtime_checks.socket, "getaddrinfo", lambda host, port, family, socktype: [])

    with pytest.raises(OSError, match="unable to open socket"):
        _open_socket("router.local", 23, 10)

    with pytest.raises(ValueError, match="target must include a host"):
        _parse_socket_target("ftp://", 21, expected_scheme="ftp")

    with pytest.raises(ValueError, match="invalid FTP response"):
        _ftp_read_response(FakeSocket([b""]))

    assert _telnet_strip_negotiation(FakeSocket([]), bytes([runtime_checks.TELNET_IAC])) == b""
    assert _telnet_strip_negotiation(FakeSocket([]), bytes([runtime_checks.TELNET_IAC, runtime_checks.TELNET_DO])) == b""
    assert runtime_checks._has_alnum_ascii("!!!") is False

    class TimeoutSocket:
        def recv(self, _size):
            raise OSError("timed out")

        def sendall(self, data):
            return None

    assert _read_until_markers(TimeoutSocket(), (b"login:",)) == b""

    class BrokenSocket:
        def recv(self, _size):
            raise OSError("broken pipe")

    with pytest.raises(OSError, match="broken pipe"):
        runtime_checks._recv_telnet_chunk(BrokenSocket())


def test_read_until_markers_covers_timeout_exception_and_marker_match(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_recv_telnet_chunk", lambda handle, size=4096: (_ for _ in ()).throw(OSError("timed out")))

    assert _read_until_markers(object(), (b"login:",)) == b""

    monkeypatch.setattr(runtime_checks, "_recv_telnet_chunk", lambda handle, size=4096: b"Login:")

    assert _read_until_markers(FakeSocket([]), (b"login:",)) == b"Login:"


def test_read_until_markers_reraises_non_timeout_socket_errors(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_recv_telnet_chunk", lambda handle, size=4096: (_ for _ in ()).throw(OSError("broken pipe")))

    with pytest.raises(OSError, match="broken pipe"):
        _read_until_markers(object(), (b"login:",))


def test_socket_target_and_protocol_helpers_cover_success_and_error_paths():
    assert _parse_socket_target("ftp://nas.example.local", 21, expected_scheme="ftp") == ("nas.example.local", 21)
    assert _parse_socket_target("telnet://switch.example.local:2323", 23, expected_scheme="telnet") == (
        "switch.example.local",
        2323,
    )
    assert _parse_socket_target("nas.example.local:2121", 21) == ("nas.example.local", 2121)
    assert _ftp_parse_pasv("227 Entering Passive Mode (192,0,2,10,4,1)") == ("192.0.2.10", 1025)
    assert _ftp_nlst_names(b"file.txt\r\nother\r\n") == ["file.txt", "other"]
    assert _ftp_nlst_names(b"\r\n") == []
    assert _looks_like_telnet_output("router> ") is True
    assert _looks_like_telnet_output("login incorrect") is False

    with pytest.raises(ValueError, match="expected ftp target"):
        _parse_socket_target("telnet://switch.example.local", 21, expected_scheme="ftp")

    with pytest.raises(ValueError, match="must include a host"):
        _parse_socket_target("", 23)


def test_portable_telnet_runner_classifies_transient_socket_failures_without_retry(monkeypatch):
    sleep_calls = []
    attempts = {"count": 0}
    monkeypatch.setattr("vivipi.runtime.checks._sleep_ms", lambda value_ms: sleep_calls.append(value_ms))

    def fake_open_socket(host, port, timeout_s):
        attempts["count"] += 1
        raise OSError(111, "connection refused")

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", fake_open_socket)

    result = portable_telnet_runner("telnet://switch.example.local", 10, trace=lambda event, **fields: None)

    assert result.ok is False
    assert result.details.startswith("refused:")
    assert attempts["count"] == 1
    assert sleep_calls == []


def test_telnet_strip_negotiation_replies_to_iac_negotiation():
    handle = FakeSocket([])

    cleaned = _telnet_strip_negotiation(handle, bytes((255, 253, 1)) + b"login: ")

    assert cleaned == b"login: "
    assert handle.sent == [bytes((255, 252, 1))]


def test_telnet_strip_negotiation_handles_will_do_subnegotiation_and_incomplete_iac():
    handle = FakeSocket([])

    cleaned = _telnet_strip_negotiation(
        handle,
        bytes((255, 251, 1)) + bytes((255, 250, 1, 2, 255, 240)) + bytes((255,)) + b"router# ",
    )

    assert cleaned == b"outer# "
    assert handle.sent == [bytes((255, 254, 1))]


def test_socket_helpers_cover_open_close_and_response_errors(monkeypatch):
    class ConnectableSocket(FakeSocket):
        def __init__(self, should_fail=False):
            super().__init__([])
            self.should_fail = should_fail
            self.timeout = None
            self.address = None

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, address):
            self.address = address
            if self.should_fail:
                raise OSError("boom")

    sockets = [ConnectableSocket(should_fail=True), ConnectableSocket()]
    monkeypatch.setattr("vivipi.runtime.checks.socket.getaddrinfo", lambda host, port, *_: [(1, 1, 1, "", (host, port)), (1, 1, 1, "", (host, port))])
    monkeypatch.setattr("vivipi.runtime.checks.socket.socket", lambda *args: sockets.pop(0))

    opened = _open_socket("nas.example.local", 21, 10)

    assert opened.timeout == 10
    assert opened.address == ("nas.example.local", 21)

    shutdown_socket = ShutdownRecordingSocket([])

    class ShutdownErrorSocket(ShutdownRecordingSocket):
        def shutdown(self, how):
            super().shutdown(how)
            raise OSError("shutdown failed")

    shutdown_error_socket = ShutdownErrorSocket([])
    trace_events = []

    _close_socket(None)
    _close_socket(shutdown_socket)
    _close_socket(shutdown_error_socket, trace=lambda event, **fields: trace_events.append((event, fields)), target="ftp://nas.example.local")
    _close_socket(CloseErrorSocket([]))

    assert shutdown_socket.shutdown_calls == [runtime_checks.socket.SHUT_RDWR]
    assert shutdown_socket.closed is True
    assert shutdown_error_socket.shutdown_calls == [runtime_checks.socket.SHUT_RDWR]
    assert shutdown_error_socket.closed is True
    assert trace_events == [("socket-close", {"stage": "close", "target": "ftp://nas.example.local"})]

    with pytest.raises(ValueError, match="invalid FTP response"):
        _ftp_read_response(FakeSocket([b"oops\r\n"]))

    with pytest.raises(ValueError, match="invalid FTP passive response"):
        _ftp_parse_pasv("227 bad response")


def test_open_socket_uses_deadline_aware_connect_and_trace(monkeypatch):
    trace_events = []

    class DeadlineSocket(FakeSocket):
        def __init__(self):
            super().__init__([])
            self.blocking = []
            self.connect_calls = 0

        def setblocking(self, enabled):
            self.blocking.append(enabled)

        def connect(self, address):
            self.connect_calls += 1
            if self.connect_calls == 1:
                raise OSError(115, "operation in progress")

    socket_handle = DeadlineSocket()
    monkeypatch.setattr(
        runtime_checks.socket,
        "getaddrinfo",
        lambda host, port, *_: [(1, 1, 1, "", (host, port))],
    )
    monkeypatch.setattr(runtime_checks.socket, "socket", lambda *args: socket_handle)
    monkeypatch.setattr(runtime_checks.select, "poll", lambda: SimpleNamespace(register=lambda handle, flags: None, poll=lambda timeout: [(0, runtime_checks.POLLOUT)]))

    opened = _open_socket(
        "nas.example.local",
        21,
        10,
        deadline=runtime_checks._deadline_after_s(10),
        trace=lambda event, **fields: trace_events.append((event, fields)),
    )

    assert opened is socket_handle
    assert socket_handle.blocking == [False]
    assert trace_events[0][0] == "dns-start"
    assert any(event == "socket-open" for event, _ in trace_events)
    assert trace_events[-1][0] == "socket-ready"


def test_read_until_markers_returns_buffer_when_stream_ends():
    handle = FakeSocket([b"hello", b" world", b""])

    assert _read_until_markers(handle, (b"missing",)) == b"hello world"


def test_portable_ftp_runner_logs_in_uses_pwd_and_quits_cleanly(monkeypatch):
    control_socket = FakeSocket(
        [
            b"220 Ready\r\n",
            b"331 Password required\r\n",
            b"230 Logged in\r\n",
            b'257 "/" is current directory\r\n',
            b"221 Goodbye\r\n",
        ]
    )

    def fake_open_socket(host, port, timeout_s, **kwargs):
        if port != 21:
            raise AssertionError((host, port))
        return control_socket

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", fake_open_socket)

    result = portable_ftp_runner(
        "ftp://nas.example.local",
        10,
        username="admin",
        password="secret",
        trace=lambda event, **fields: None,
    )

    assert result.ok is True
    assert result.details == "pwd=/"
    assert control_socket.sent == [
        b"USER admin\r\n",
        b"PASS secret\r\n",
        b"PWD\r\n",
        b"QUIT\r\n",
    ]
    assert control_socket.closed is True


def test_portable_http_runner_uses_manual_socket_deadline_path_on_micropython(monkeypatch):
    class FakeMicroPythonTime:
        def __init__(self):
            self.now_ms = 0

        def ticks_ms(self):
            return self.now_ms

        def ticks_add(self, value, delta):
            return value + delta

        def ticks_diff(self, left, right):
            return left - right

        def perf_counter(self):
            return self.now_ms / 1000.0

    class RecordingSocket(FakeSocket):
        def __init__(self, responses):
            super().__init__(responses)
            self.recv_sizes = []

        def recv(self, size):
            self.recv_sizes.append(size)
            return super().recv(size)

    handle = RecordingSocket([b"HTTP/1.0 200 OK\r\nContent-Length: 12\r\n\r\n{\"ok\": true}", b""])
    fake_time = FakeMicroPythonTime()

    monkeypatch.setattr(runtime_checks, "time", fake_time)
    monkeypatch.setattr(runtime_checks, "urlparse", lambda value: SimpleNamespace(scheme="http", hostname="nas.example.local", port=None))
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s, **kwargs: handle)

    result = portable_http_runner("GET", "http://nas.example.local/health?view=1", 8)

    assert result.status_code == 200
    assert result.body == {"ok": True}
    assert handle.sent[0].startswith(b"GET /health?view=1 HTTP/1.1\r\n")
    assert max(handle.recv_sizes) == runtime_checks.DEVICE_SOCKET_RECV_CHUNK_SIZE


def test_portable_ftp_runner_caps_manual_socket_read_size_on_micropython(monkeypatch):
    class FakeMicroPythonTime:
        def __init__(self):
            self.now_ms = 0

        def ticks_ms(self):
            return self.now_ms

        def ticks_add(self, value, delta):
            return value + delta

        def ticks_diff(self, left, right):
            return left - right

        def perf_counter(self):
            return self.now_ms / 1000.0

    class RecordingSocket(FakeSocket):
        def __init__(self, responses):
            super().__init__(responses)
            self.recv_sizes = []

        def recv(self, size):
            self.recv_sizes.append(size)
            return super().recv(size)

    control_socket = RecordingSocket(
        [
            b"220 Ready\r\n",
            b"331 Password required\r\n",
            b"230 Logged in\r\n",
            b'257 "/" is current directory\r\n',
            b"221 Goodbye\r\n",
        ]
    )
    fake_time = FakeMicroPythonTime()

    monkeypatch.setattr(runtime_checks, "time", fake_time)
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s, **kwargs: control_socket)

    result = portable_ftp_runner(
        "ftp://nas.example.local",
        10,
        username="admin",
        password="secret",
        trace=lambda event, **fields: None,
    )

    assert result.ok is True
    assert result.details == "pwd=/"
    assert max(control_socket.recv_sizes) == runtime_checks.DEVICE_SOCKET_RECV_CHUNK_SIZE


def test_portable_ftp_runner_rejects_invalid_greeting(monkeypatch):
    control_socket = FakeSocket([b"500 Down\r\n"])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: control_socket)

    result = portable_ftp_runner("nas.example.local:21", 10, trace=lambda event, **fields: None)

    assert result.ok is False
    assert result.details == "expected FTP 220, got 500 Down"
    assert control_socket.sent == []


def test_portable_ftp_runner_reports_greeting_failures(monkeypatch):
    def run_case(responses):
        control_socket = FakeSocket(responses)
        monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: control_socket)
        return portable_ftp_runner(
            "ftp://nas.example.local",
            10,
            username="admin",
            password="secret",
            trace=lambda event, **fields: None,
        )

    greeting_failure = run_case([b"421 Down\r\n"])

    assert greeting_failure.details == "expected FTP 220, got 421 Down"


def test_portable_ftp_runner_reports_login_failures(monkeypatch):
    control_socket = FakeSocket(
        [
            b"220 Ready\r\n",
            b"331 Password required\r\n",
            b"530 Not logged in\r\n",
            b"221 Goodbye\r\n",
        ]
    )
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: control_socket)

    result = portable_ftp_runner(
        "ftp://nas.example.local",
        10,
        username="admin",
        password="secret",
        trace=lambda event, **fields: None,
    )

    assert result.ok is False
    assert result.details == "expected FTP 230, got 530 Not logged in"
    assert control_socket.sent == [
        b"USER admin\r\n",
        b"PASS secret\r\n",
    ]
    assert control_socket.closed is True


def test_portable_ftp_runner_reports_socket_errors(monkeypatch):
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: (_ for _ in ()).throw(OSError("refused")))

    result = portable_ftp_runner("ftp://nas.example.local", 10, trace=lambda event, **fields: None)

    assert result.ok is False
    assert result.details == "refused"


def test_portable_telnet_runner_accepts_non_empty_response(monkeypatch):
    class BannerSocket(FakeSocket):
        def __init__(self):
            super().__init__([
                b'\xff\xfe"\xff\xfb\x01\x1bc\x1b[0;37;2m\x1b[1;1H\x1bc\x1b[0;37;2m\x1b[1;1H\x1b[1;6H\x1b[0;37;1m*** C64 Ultimate (V1.49) 1.1.0 *** Remote ***\x1b[24;53H\x1b(BF7=HELP'
            ])
            self.recv_calls = 0
            self.timeout_values = []

        def settimeout(self, value):
            self.timeout_values.append(value)

        def recv(self, _size):
            self.recv_calls += 1
            if self.recv_calls == 1:
                return super().recv(_size)
            raise OSError("timed out")

    handle = BannerSocket()
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner(
        "telnet://switch.example.local",
        10,
        username="ops",
        password="secret",
        trace=lambda event, **fields: None,
    )

    assert result.ok is True
    assert result.details == "response-received"
    assert result.metadata["close_reason"] == "idle-timeout"
    assert result.metadata["response_received"] is True
    assert handle.sent == [bytes((255, 252, 34)), bytes((255, 254, 1))]
    assert handle.closed is True


def test_portable_telnet_runner_rejects_login_failure_text(monkeypatch):
    handle = FakeSocket([b"Login incorrect\r\n", b""])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10, trace=lambda event, **fields: None)

    assert result.ok is False
    assert result.details == "telnet failure marker present"
    assert handle.sent == []


def test_portable_telnet_runner_rejects_blank_sessions(monkeypatch):
    handle = FakeSocket([b"   \r\n", b""])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10, trace=lambda event, **fields: None)

    assert result.ok is False
    assert result.status == Status.FAIL
    assert result.details == "closed immediately"
    assert handle.sent == []


def test_portable_telnet_runner_rejects_stable_idle_open_without_response(monkeypatch):
    class TimeoutThenResponseSocket(FakeSocket):
        def __init__(self):
            super().__init__([])
            self.recv_calls = 0
            self.timeout_values = []

        def settimeout(self, value):
            self.timeout_values.append(value)

        def recv(self, _size):
            self.recv_calls += 1
            raise OSError("timed out")

    handle = TimeoutThenResponseSocket()
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10, trace=lambda event, **fields: None)

    assert result.ok is False
    assert result.status == Status.FAIL
    assert result.details == "no telnet response"
    assert result.metadata["close_reason"] == "idle-timeout"
    assert result.metadata["handshake_detected"] is False
    assert result.metadata["response_received"] is False
    assert result.metadata["session_duration_ms"] >= runtime_checks.TELNET_STABLE_OPEN_THRESHOLD_MS
    assert handle.recv_calls == 5
    assert handle.timeout_values == [runtime_checks.TELNET_IDLE_TIMEOUT_S] * 5
    assert handle.sent == []


def test_portable_telnet_runner_accepts_password_prompt_response(monkeypatch):
    class PasswordPromptSocket(FakeSocket):
        def __init__(self):
            super().__init__([b"Password: \xff\xfb\x01", b"\r\nREADY\r\n"])
            self.recv_calls = 0
            self.timeout_values = []

        def settimeout(self, value):
            self.timeout_values.append(value)

        def recv(self, _size):
            self.recv_calls += 1
            if self.recv_calls <= 2:
                return super().recv(_size)
            raise OSError("timed out")

    handle = PasswordPromptSocket()
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10, password="secret", trace=lambda event, **fields: None)

    assert result.ok is True
    assert result.status == Status.OK
    assert result.details == "response-received"
    assert result.metadata["close_reason"] == "idle-timeout"
    assert result.metadata["handshake_detected"] is True
    assert result.metadata["response_received"] is True
    assert handle.sent == [bytes((255, 254, 1))]


def test_portable_telnet_runner_tolerates_negotiation_reply_timeout(monkeypatch):
    class ReplyTimeoutSocket(FakeSocket):
        def __init__(self, responses):
            super().__init__(responses)
            self.reply_attempts = 0
            self.recv_calls = 0

        def sendall(self, data):
            if data != b"\r\n":
                self.reply_attempts += 1
                raise OSError(110, "timed out")
            super().sendall(data)

        def recv(self, _size):
            self.recv_calls += 1
            if self.recv_calls <= 2:
                return super().recv(_size)
            raise OSError("timed out")

    handle = ReplyTimeoutSocket([b"Password: \xff\xfb\x01", b"\r\nREADY\r\n"])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10, trace=lambda event, **fields: None)

    assert result.ok is True
    assert result.status == Status.OK
    assert result.details == "response-received"
    assert handle.sent == []
    assert handle.reply_attempts == 1


def test_portable_telnet_runner_waits_for_response_drain_before_success(monkeypatch):
    class DrainingBannerSocket(FakeSocket):
        def __init__(self):
            super().__init__([
                b'\xff\xfe"\xff\xfb\x01\x1bc\x1b[0;37;2m\x1b[1;1H\x1bc\x1b[0;37;2m\x1b[1;1H\x1b[1;4H\x1b[0;37;1m*** Ultimate 64 Elite (V1.4B) 3.14e *** Remote ***\x1b[24;53H\x1b(BF3=HELP',
                b"extra trailing repaint bytes that should never be read",
            ])
            self.recv_calls = 0
            self.timeout_values = []

        def settimeout(self, value):
            self.timeout_values.append(value)

        def recv(self, _size):
            self.recv_calls += 1
            return super().recv(_size)

    handle = DrainingBannerSocket()
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10, trace=lambda event, **fields: None)

    assert result.ok is True
    assert result.status == Status.OK
    assert result.details == "response-received"
    assert result.metadata["close_reason"] == "remote-close"
    assert result.metadata["response_received"] is True
    assert result.metadata["handshake_detected"] is True
    assert handle.recv_calls == 3
    assert handle.timeout_values == [runtime_checks.TELNET_IDLE_TIMEOUT_S, runtime_checks.TELNET_POST_DATA_IDLE_TIMEOUT_S, runtime_checks.TELNET_POST_DATA_IDLE_TIMEOUT_S]


def test_portable_telnet_runner_rejects_explicit_failure_text_and_reports_socket_error(monkeypatch):
    handle = FakeSocket([b"Access denied\r\n", b""])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    failed_login = portable_telnet_runner("telnet://switch.example.local", 10, trace=lambda event, **fields: None)

    assert failed_login.ok is False
    assert failed_login.status == Status.FAIL
    assert failed_login.details == "telnet failure marker present"

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: (_ for _ in ()).throw(OSError("refused")))

    socket_failure = portable_telnet_runner("telnet://switch.example.local", 10, trace=lambda event, **fields: None)

    assert socket_failure.ok is False
    assert socket_failure.status == Status.FAIL
    assert socket_failure.details == "refused"


def test_read_telnet_until_idle_counts_visible_bytes_without_buffering_full_transcript():
    class LargeBannerSocket(FakeSocket):
        def __init__(self):
            super().__init__([b"A" * 2048, b"B" * 1024, b""])
            self.timeout_values = []

        def settimeout(self, value):
            self.timeout_values.append(value)

        def recv(self, _size):
            raise AssertionError("recv should not be used when recv_into is available")

        def recv_into(self, buffer):
            if not self._responses:
                return 0
            chunk = self._responses.pop(0)
            size = len(chunk)
            buffer[:size] = chunk
            return size

    handle = LargeBannerSocket()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: True)

    try:
        session = runtime_checks._read_telnet_until_idle(handle)
    finally:
        monkeypatch.undo()

    assert session["visible_bytes"] == 3072
    assert session["has_visible_text"] is True
    assert session["handshake_detected"] is False
    assert session["close_reason"] == "remote-close"
    assert handle.timeout_values == [
        runtime_checks.TELNET_IDLE_TIMEOUT_S,
        runtime_checks.TELNET_POST_DATA_IDLE_TIMEOUT_S,
        runtime_checks.TELNET_POST_DATA_IDLE_TIMEOUT_S,
    ]


def test_read_telnet_until_idle_detects_failure_markers_across_chunk_boundaries():
    handle = FakeSocket([b"Access den", b"ied\r\n", b""])

    session = runtime_checks._read_telnet_until_idle(handle)

    assert session["failure_detected"] is True
    assert session["close_reason"] == "failure-marker"


def test_read_telnet_until_idle_tracks_visible_text_for_banners_with_telnet_negotiation():
    handle = FakeSocket(
        [
            b'\xff\xfe"\xff\xfb\x01\x1bc\x1b[0;37;2m\x1b[1;1H\x1bc\x1b[0;37;2m\x1b[1;1H\x1b[1;6H\x1b[0;37;1m*** C64 Ultimate (V1.49) 1.1.0 *** Remote ***\x1b[24;53H\x1b(BF7=HELP',
            b"",
        ]
    )

    session = runtime_checks._read_telnet_until_idle(handle)

    assert session["handshake_detected"] is True
    assert session["has_visible_text"] is True
    assert session["visible_bytes"] > 0
    assert session["close_reason"] == "remote-close"


def test_probe_budget_exhaustion_raises_timeout():
    budget = runtime_checks._ProbeBudget(max_ops=2, pacing_ms=0)
    budget.charge()
    budget.charge()
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        budget.charge()


def test_probe_budget_zero_count_and_probe_activity_callback_are_noops():
    activity = []

    runtime_checks.set_probe_activity_callback(lambda: activity.append("tick"))
    runtime_checks._emit_probe_activity()
    runtime_checks.set_probe_activity_callback(None)
    runtime_checks._emit_probe_activity()

    budget = runtime_checks._ProbeBudget(max_ops=2, pacing_ms=0)
    budget.charge(0)

    assert activity == ["tick"]
    assert budget.remaining == 2


def test_bounded_operation_returns_none_for_empty_or_whitespace():
    assert runtime_checks._bounded_operation(None) is None
    assert runtime_checks._bounded_operation("") is None
    assert runtime_checks._bounded_operation("   ") is None


def test_bounded_operation_truncates_to_limit():
    long = "X" * (runtime_checks.PROBE_OPERATION_LIMIT + 20)
    truncated = runtime_checks._bounded_operation(long)
    assert truncated is not None
    assert len(truncated) == runtime_checks.PROBE_OPERATION_LIMIT
    assert truncated.endswith("…")


def test_bounded_operation_honors_single_character_limit(monkeypatch):
    monkeypatch.setattr(runtime_checks, "PROBE_OPERATION_LIMIT", 1)

    assert runtime_checks._bounded_operation("HELLO") == "H"


def test_bounded_operation_collapses_internal_whitespace():
    assert runtime_checks._bounded_operation("GET\r\n  /v1/checks\t now") == "GET /v1/checks now"


def test_ftp_operation_descriptor_redacts_password():
    assert runtime_checks._ftp_operation_descriptor("PASS hunter2") == "PASS ***"
    assert runtime_checks._ftp_operation_descriptor("pass anything") == "PASS ***"
    assert runtime_checks._ftp_operation_descriptor("USER anonymous") == "USER anonymous"
    assert runtime_checks._ftp_operation_descriptor("PWD") == "PWD"
    assert runtime_checks._ftp_operation_descriptor("") == "FTP"


def test_socket_sendall_includes_operation_when_provided(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="send": None)

    class SendallSocket:
        def sendall(self, payload):
            return None

    events = []
    runtime_checks._socket_sendall(
        SendallSocket(),
        b"GET /v1/checks HTTP/1.1\r\n",
        ("perf", 1.0),
        trace=lambda event, **fields: events.append((event, fields)),
        stage="http-send",
        operation="GET /v1/checks",
        target="service.example.local:80",
    )
    assert events == [
        (
            "socket-send",
            {"stage": "http-send", "operation": "GET /v1/checks", "target": "service.example.local:80", "bytes_sent": 25},
        ),
    ]


def test_socket_sendall_omits_operation_when_absent(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="send": None)

    class SendallSocket:
        def sendall(self, payload):
            return None

    events = []
    runtime_checks._socket_sendall(
        SendallSocket(),
        b"hello",
        ("perf", 1.0),
        trace=lambda event, **fields: events.append((event, fields)),
        stage="send",
    )
    assert events == [("socket-send", {"stage": "send", "bytes_sent": 5})]


def test_socket_sendall_charges_budget_on_successful_send(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="send": None)
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class SendallSocket:
        def sendall(self, payload):
            return None

    budget = runtime_checks._ProbeBudget(max_ops=1, pacing_ms=0)
    runtime_checks._socket_sendall(SendallSocket(), b"x", ("perf", 1.0), stage="send", budget=budget)
    assert budget.remaining == 0
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._socket_sendall(SendallSocket(), b"x", ("perf", 1.0), stage="send", budget=budget)


def test_socket_sendall_exhausts_budget_mid_loop_on_partial_sends(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="send": None)
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class PartialSender:
        def __init__(self):
            self.calls = 0

        def send(self, payload):
            self.calls += 1
            return 1

    handle = PartialSender()
    budget = runtime_checks._ProbeBudget(max_ops=2, pacing_ms=0)
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._socket_sendall(handle, b"abc", ("perf", 1.0), stage="send", budget=budget)
    assert handle.calls == 2


def test_socket_sendall_exhausts_budget_on_would_block_storm(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="send": None)
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class WouldBlockSender:
        def __init__(self):
            self.calls = 0

        def send(self, payload):
            self.calls += 1
            raise OSError("would block")

    handle = WouldBlockSender()
    budget = runtime_checks._ProbeBudget(max_ops=2, pacing_ms=0)
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._socket_sendall(handle, b"abc", ("perf", 1.0), stage="send", budget=budget)
    assert handle.calls == 2


def test_socket_recv_charges_budget(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="recv": None)
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class RecvSocket:
        def recv(self, size):
            return b"ok"

    budget = runtime_checks._ProbeBudget(max_ops=1, pacing_ms=0)
    runtime_checks._socket_recv(RecvSocket(), 4096, ("perf", 1.0), stage="recv", budget=budget)
    assert budget.remaining == 0
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._socket_recv(RecvSocket(), 4096, ("perf", 1.0), stage="recv", budget=budget)


def test_socket_recv_exhausts_budget_on_would_block_storm(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="recv": None)
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class WouldBlockRecvSocket:
        def __init__(self):
            self.calls = 0

        def recv(self, size):
            self.calls += 1
            raise OSError("would block")

    handle = WouldBlockRecvSocket()
    budget = runtime_checks._ProbeBudget(max_ops=2, pacing_ms=0)
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._socket_recv(handle, 4096, ("perf", 1.0), stage="recv", budget=budget)
    assert handle.calls == 2


def test_recv_until_closed_exhausts_budget_on_slow_drip_peer(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_socket_wait", lambda handle, deadline, writable, trace=None, stage="recv": None)
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class SlowDripSocket:
        def __init__(self):
            self.calls = 0

        def recv(self, size):
            self.calls += 1
            return b"x"

    handle = SlowDripSocket()
    budget = runtime_checks._ProbeBudget(max_ops=3, pacing_ms=0)
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._recv_until_closed(handle, ("perf", 1.0), stage="http-recv", budget=budget)
    assert handle.calls == 3


def test_socket_wait_falls_back_to_local_wait_accounting_when_deadline_stalls(monkeypatch):
    poll_calls = []

    class FakePoller:
        def register(self, handle, flags):
            return None

        def poll(self, wait_ms):
            poll_calls.append(wait_ms)
            return []

    monkeypatch.setattr(runtime_checks.select, "poll", lambda: FakePoller())
    remaining_values = iter((1500, 1500, 0))
    monkeypatch.setattr(runtime_checks, "_deadline_remaining_ms", lambda deadline: next(remaining_values))

    with pytest.raises(TimeoutError, match="timed out"):
        runtime_checks._socket_wait(object(), ("perf", 1.0), writable=False, stage="recv")

    assert poll_calls == [1000, 500]


def test_read_telnet_until_idle_terminates_at_max_recv_chunks(monkeypatch):
    class ChattyPeer:
        def __init__(self):
            self.calls = 0

        def settimeout(self, value):
            return None

        def recv(self, _size):
            self.calls += 1
            return b"." * 64

        def sendall(self, payload):
            return None

    handle = ChattyPeer()
    session = runtime_checks._read_telnet_until_idle(handle, max_chunks=4)
    assert handle.calls == 4
    assert session["visible_bytes"] <= 4 * 64
    assert session["has_visible_text"] is True
    assert session["close_reason"] == "chunk-limit"


def test_read_telnet_until_idle_respects_deadline(monkeypatch):
    class ChattyPeer:
        def settimeout(self, value):
            return None

        def recv(self, _size):
            return b"banner"

        def sendall(self, payload):
            return None

    handle = ChattyPeer()
    session = runtime_checks._read_telnet_until_idle(
        handle,
        deadline=("perf", -1.0),
    )
    assert session["visible_bytes"] == 0
    assert session["has_visible_text"] is False
    assert session["close_reason"] == "deadline"


def test_read_telnet_until_idle_stops_when_deadline_expires_mid_loop(monkeypatch):
    remaining_ms = iter((5, 0))
    monkeypatch.setattr(runtime_checks, "_deadline_remaining_ms", lambda deadline: next(remaining_ms, 0))

    class ChattyPeer:
        def __init__(self):
            self.calls = 0

        def settimeout(self, value):
            return None

        def recv(self, _size):
            self.calls += 1
            return b"banner"

        def sendall(self, payload):
            return None

    handle = ChattyPeer()
    session = runtime_checks._read_telnet_until_idle(handle, deadline=("perf", 1.0), max_chunks=16)
    assert handle.calls == 1
    assert session["visible_bytes"] == 6
    assert session["has_visible_text"] is True
    assert session["close_reason"] == "deadline"


def test_read_telnet_until_idle_charges_budget_per_chunk(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class ChattyPeer:
        def __init__(self):
            self.calls = 0

        def settimeout(self, value):
            return None

        def recv(self, _size):
            self.calls += 1
            return b"banner"

        def sendall(self, payload):
            return None

    handle = ChattyPeer()
    budget = runtime_checks._ProbeBudget(max_ops=2, pacing_ms=0)
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._read_telnet_until_idle(handle, budget=budget, max_chunks=16)


def test_read_telnet_until_idle_exhausts_budget_during_iac_storm(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class IacStormPeer:
        def __init__(self):
            self.recv_calls = 0
            self.send_calls = 0

        def settimeout(self, value):
            return None

        def recv(self, _size):
            self.recv_calls += 1
            return bytes((255, 253, 1, 255, 253, 3, 255, 253, 5))

        def sendall(self, payload):
            self.send_calls += 1
            raise OSError(110, "ETIMEDOUT")

    handle = IacStormPeer()
    budget = runtime_checks._ProbeBudget(max_ops=2, pacing_ms=0)
    with pytest.raises(TimeoutError, match="probe io budget exhausted"):
        runtime_checks._read_telnet_until_idle(handle, budget=budget, max_chunks=16)
    assert handle.recv_calls == 1
    assert handle.send_calls == 1


def test_telnet_strip_negotiation_uses_budgeted_telnet_send_path(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_sleep_ms", lambda value_ms: None)

    class Sink:
        def __init__(self):
            self.sent = []

        def sendall(self, data):
            self.sent.append(bytes(data))

    handle = Sink()
    events = []
    budget = runtime_checks._ProbeBudget(max_ops=1, pacing_ms=0)
    cleaned = runtime_checks._telnet_strip_negotiation(
        handle,
        bytes((255, 253, 1)) + b"login: ",
        trace=lambda event, **fields: events.append((event, fields)),
        budget=budget,
    )
    assert cleaned == b"login: "
    assert handle.sent == [bytes((255, 252, 1))]
    assert budget.remaining == 0
    assert events == [("socket-send", {"stage": "telnet-send", "operation": "telnet-iac", "bytes_sent": 3})]


def test_portable_http_runner_emits_method_and_path_operation(monkeypatch):
    handle = FakeSocket([b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nOK", b""])

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s, **kwargs: handle)

    send_events: list[dict[str, object]] = []

    def trace(event, **fields):
        if event == "socket-send":
            send_events.append(fields)

    result = runtime_checks._portable_http_runner_socket(
        "GET",
        "http://service.example.local/v1/checks",
        5,
        trace=trace,
    )
    assert result.status_code == 200
    assert send_events, "expected a socket-send event"
    assert send_events[0].get("operation") == "GET /v1/checks"
    assert send_events[0].get("stage") == "http-send"
    assert send_events[0].get("target") == "service.example.local:80"


def test_portable_ftp_runner_emits_redacted_pass_operation(monkeypatch):
    def scripted(responses):
        return FakeSocket(responses)

    responses = [
        b"220 welcome\r\n",
        b"331 need password\r\n",
        b"230 ok\r\n",
        b"257 \"/home/user\"\r\n",
        b"221 bye\r\n",
    ]
    handle = scripted(responses)
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s, **kwargs: handle)
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: True)

    send_events: list[dict[str, object]] = []

    def trace(event, **fields):
        if event == "socket-send":
            send_events.append(fields)

    result = portable_ftp_runner("ftp://service.example.local", 5, username="alice", password="hunter2", trace=trace)
    assert result.ok is True
    operations = [fields.get("operation") for fields in send_events]
    assert "USER alice" in operations
    assert "PASS ***" in operations
    assert "PWD" in operations
    assert "QUIT" in operations
    for fields in send_events:
        assert fields.get("stage") == "ftp-send"
        assert fields.get("target") == "service.example.local:21"
        assert "hunter2" not in str(fields.get("operation", ""))


def test_portable_ftp_runner_emits_endpoint_and_operation_on_recv(monkeypatch):
    handle = FakeSocket(
        [
            b"220 welcome\r\n",
            b"331 need password\r\n",
            b"230 ok\r\n",
            b"257 \"/home/user\"\r\n",
            b"221 bye\r\n",
        ]
    )
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s, **kwargs: handle)
    monkeypatch.setattr(runtime_checks, "_is_micropython_runtime", lambda: True)

    recv_events: list[dict[str, object]] = []

    def trace(event, **fields):
        if event == "socket-recv":
            recv_events.append(fields)

    result = portable_ftp_runner("ftp://service.example.local", 5, username="alice", password="hunter2", trace=trace)
    assert result.ok is True
    assert recv_events == [
        {"stage": "ftp-recv", "operation": "server-greeting", "target": "service.example.local:21", "bytes_received": 13},
        {"stage": "ftp-recv", "operation": "USER alice", "target": "service.example.local:21", "bytes_received": 19},
        {"stage": "ftp-recv", "operation": "PASS ***", "target": "service.example.local:21", "bytes_received": 8},
        {"stage": "ftp-recv", "operation": "PWD", "target": "service.example.local:21", "bytes_received": 18},
        {"stage": "ftp-recv", "operation": "QUIT", "target": "service.example.local:21", "bytes_received": 9},
    ]


def test_telnet_send_best_effort_emits_telnet_iac_operation():
    class Sink:
        def __init__(self):
            self.sent = []

        def sendall(self, data):
            self.sent.append(bytes(data))

    handle = Sink()
    events: list[tuple[str, dict[str, object]]] = []
    sent_ok = runtime_checks._telnet_send_best_effort(
        handle,
        bytes((255, 252, 1)),
        trace=lambda event, **fields: events.append((event, fields)),
        operation="telnet-iac",
    )
    assert sent_ok is True
    assert events == [
        ("socket-send", {"stage": "telnet-send", "operation": "telnet-iac", "bytes_sent": 3}),
    ]


def test_read_telnet_until_idle_emits_endpoint_and_operation_on_recv():
    class PromptPeer:
        def __init__(self):
            self.responses = [b"router> ", b""]

        def settimeout(self, value):
            return None

        def recv(self, _size):
            return self.responses.pop(0)

        def sendall(self, payload):
            return None

    events: list[tuple[str, dict[str, object]]] = []
    session = runtime_checks._read_telnet_until_idle(
        PromptPeer(),
        trace=lambda event, **fields: events.append((event, fields)),
        target="switch.example.local:23",
    )

    assert session["visible_bytes"] == 7
    assert session["has_visible_text"] is True
    assert events[0] == (
        "socket-recv",
        {"stage": "telnet-recv", "operation": "read-visible", "target": "switch.example.local:23", "bytes_received": 8},
    )


def test_read_telnet_until_idle_reports_collect_visible_socket_errors(monkeypatch):
    class ChunkSocket(FakeSocket):
        def __init__(self):
            super().__init__([b"Welcome\r\n"])
            self.timeout_values = []

        def settimeout(self, value):
            self.timeout_values.append(value)

    handle = ChunkSocket()
    monkeypatch.setattr(runtime_checks, "_telnet_collect_visible", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("refused")))

    session = runtime_checks._read_telnet_until_idle(handle)

    assert session["failure_detected"] is False
    assert session["close_reason"] == "refused"


def test_read_telnet_until_idle_reports_non_budget_timeout_from_collect_visible(monkeypatch):
    class ChunkSocket(FakeSocket):
        def __init__(self):
            super().__init__([b"Welcome\r\n"])
            self.timeout_values = []

        def settimeout(self, value):
            self.timeout_values.append(value)

    handle = ChunkSocket()
    monkeypatch.setattr(runtime_checks, "_telnet_collect_visible", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timed out")))

    session = runtime_checks._read_telnet_until_idle(handle)

    assert session["failure_detected"] is False
    assert session["close_reason"] == "timeout"


def test_read_telnet_until_idle_reports_stable_open_before_first_read(monkeypatch):
    monkeypatch.setattr(runtime_checks, "_start_timer", lambda: (object(), False))
    monkeypatch.setattr(runtime_checks, "_elapsed_ms", lambda started_at, uses_ticks_ms: runtime_checks.TELNET_STABLE_OPEN_THRESHOLD_MS)

    session = runtime_checks._read_telnet_until_idle(object())

    assert session == {
        "visible_bytes": 0,
        "has_visible_text": False,
        "handshake_detected": False,
        "failure_detected": False,
        "close_reason": "stable-open",
        "session_duration_ms": runtime_checks.TELNET_STABLE_OPEN_THRESHOLD_MS,
    }


def test_read_telnet_until_idle_retries_timeout_after_meaningful_interaction(monkeypatch):
    class VisibleThenTimeoutSocket:
        def __init__(self):
            self.timeout_values = []
            self.recv_calls = 0

        def settimeout(self, value):
            self.timeout_values.append(value)

        def recv(self, _size):
            self.recv_calls += 1
            if self.recv_calls == 1:
                return b"Welcome\r\n"
            raise TimeoutError("timed out")

    elapsed_values = iter((0.0, 0.0, 90.0, 150.0, 250.0, 510.0, 510.0, 510.0))
    monkeypatch.setattr(runtime_checks, "_start_timer", lambda: (object(), False))
    monkeypatch.setattr(runtime_checks, "_elapsed_ms", lambda started_at, uses_ticks_ms: next(elapsed_values))

    handle = VisibleThenTimeoutSocket()
    session = runtime_checks._read_telnet_until_idle(handle, quiet_timeout_s=0.01)

    assert session["close_reason"] == "idle-timeout"
    assert session["has_visible_text"] is True
    assert handle.recv_calls >= 3


def test_read_telnet_until_idle_retries_oserror_timeout_after_meaningful_interaction(monkeypatch):
    class VisibleThenOSErrorTimeoutSocket:
        def __init__(self):
            self.timeout_values = []
            self.recv_calls = 0

        def settimeout(self, value):
            self.timeout_values.append(value)

        def recv(self, _size):
            self.recv_calls += 1
            if self.recv_calls == 1:
                return b"READY>"
            raise OSError("timed out")

    elapsed_values = iter((0.0, 0.0, 90.0, 150.0, 250.0, 510.0, 510.0, 510.0))
    monkeypatch.setattr(runtime_checks, "_start_timer", lambda: (object(), False))
    monkeypatch.setattr(runtime_checks, "_elapsed_ms", lambda started_at, uses_ticks_ms: next(elapsed_values))

    handle = VisibleThenOSErrorTimeoutSocket()
    session = runtime_checks._read_telnet_until_idle(handle, quiet_timeout_s=0.01)

    assert session["close_reason"] == "idle-timeout"
    assert session["has_visible_text"] is True
    assert handle.recv_calls >= 3


def test_read_telnet_until_idle_continues_after_handshake_only_chunks(monkeypatch):
    class HandshakeOnlySocket(FakeSocket):
        def __init__(self):
            super().__init__([bytes((255, 251, 1)), b""])
            self.timeout_values = []

        def settimeout(self, value):
            self.timeout_values.append(value)

    monkeypatch.setattr(
        runtime_checks,
        "_telnet_send_best_effort",
        lambda handle, payload, trace=None, operation=None, target=None, budget=None: True,
    )

    handle = HandshakeOnlySocket()
    session = runtime_checks._read_telnet_until_idle(handle)

    assert session["close_reason"] == "remote-close"
    assert session["handshake_detected"] is True
    assert session["has_visible_text"] is False
