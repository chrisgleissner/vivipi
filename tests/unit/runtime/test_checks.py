import sys
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from vivipi.core.execution import PingProbeResult
from vivipi.core.models import CheckType, Status
import vivipi.runtime.checks as runtime_checks
from vivipi.runtime.checks import (
    _close_socket,
    _classify_network_error,
    _ftp_read_response,
    _ftp_parse_pasv,
    _format_network_error,
    _is_retryable_network_error,
    _looks_like_ftp_listing,
    _looks_like_telnet_output,
    _normalize_error_text,
    _open_socket,
    _parse_socket_target,
    _read_until_markers,
    _recv_all,
    _retry_attempts,
    _retry_backoff_ms,
    _retry_network_operation,
    _retry_probe_result,
    _runtime_optional_auth,
    _should_retry_probe_result,
    _sleep_ms,
    _telnet_strip_negotiation,
    build_executor,
    build_runtime_definitions,
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


def test_portable_http_runner_retries_transient_transport_errors(monkeypatch):
    sleep_calls = []
    attempts = {"count": 0}

    def fake_request(method, target, timeout):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OSError("timed out")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"checks": []},
            text="{\"checks\": []}",
            close=lambda: None,
        )

    monkeypatch.setitem(sys.modules, "urequests", SimpleNamespace(request=fake_request))
    monkeypatch.setattr("vivipi.runtime.checks._sleep_ms", lambda value_ms: sleep_calls.append(value_ms))

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.status_code == 200
    assert attempts["count"] == 3
    assert sleep_calls == [100, 200]


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


def test_portable_http_runner_uses_urllib_fallback_for_success_and_http_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "urequests", raising=False)

    class FakeSuccessResponse:
        def __init__(self, body, status_code):
            self._body = body
            self._status_code = status_code

        def read(self):
            return self._body.encode("utf-8")

        def getcode(self):
            return self._status_code

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen_success(request, timeout):
        return FakeSuccessResponse('{"checks": []}', 200)

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_success)

    success = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert success.status_code == 200
    assert success.body == {"checks": []}

    class FakeErrorResponse:
        def read(self):
            return b"plain error"

        def close(self):
            return None

    def fake_urlopen_error(request, timeout):
        raise HTTPError(request.full_url, 503, "down", hdrs=None, fp=FakeErrorResponse())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_error)

    failure = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert failure.status_code == 503
    assert failure.body == "plain error"


def test_portable_http_runner_returns_classified_transport_error_after_retries(monkeypatch):
    monkeypatch.setitem(sys.modules, "urequests", SimpleNamespace(request=lambda method, target, timeout: (_ for _ in ()).throw(OSError("network is unreachable"))))
    monkeypatch.setattr("vivipi.runtime.checks._sleep_ms", lambda value_ms: None)

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.status_code is None
    assert result.details.startswith("network:")


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


def test_portable_ping_runner_retries_transient_failures(monkeypatch):
    monkeypatch.delitem(sys.modules, "uping", raising=False)

    import subprocess

    sleep_calls = []
    responses = iter(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="timeout"),
            SimpleNamespace(returncode=1, stdout="", stderr="timeout"),
            SimpleNamespace(returncode=0, stdout="64 bytes from host time=3.2", stderr=""),
        ]
    )

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("vivipi.runtime.checks._sleep_ms", lambda value_ms: sleep_calls.append(value_ms))

    result = portable_ping_runner("192.168.1.1", 10)

    assert result.ok is True
    assert sleep_calls == [100, 200]


def test_network_retry_helpers_cover_edge_cases(monkeypatch):
    fake_time = SimpleNamespace(sleep_calls=[], sleep_ms=lambda value_ms: fake_time.sleep_calls.append(value_ms))
    monkeypatch.setattr(runtime_checks, "time", fake_time)

    _sleep_ms(0)
    _sleep_ms(5)

    assert fake_time.sleep_calls == [5]
    assert _retry_attempts(0) == 1
    assert _retry_backoff_ms(6) == 800
    assert _normalize_error_text(RuntimeError()) == "RuntimeError"
    assert _classify_network_error(TimeoutError("timed out")) == "timeout"
    assert _classify_network_error(OSError(-2, "name resolution failed")) == "dns"
    assert _classify_network_error(OSError(111, "connection refused")) == "refused"
    assert _classify_network_error(OSError(101, "network unreachable")) == "network"
    assert _classify_network_error(OSError("broken pipe")) == "reset"
    assert _format_network_error(OSError("broken pipe")).startswith("reset:")
    assert _is_retryable_network_error(RuntimeError("boom")) is False
    assert _runtime_optional_auth(None) is None
    assert _runtime_optional_auth("  admin  ") == "admin"
    assert _should_retry_probe_result("invalid output") is False
    assert _retry_probe_result(lambda: PingProbeResult(ok=False, details="invalid output"), 10).details == "invalid output"
    assert _retry_network_operation(lambda: "ok", 10) == "ok"
    with pytest.raises(RuntimeError, match="boom"):
        _retry_network_operation(lambda: (_ for _ in ()).throw(RuntimeError("boom")), 10)
    assert _parse_socket_target("nas.example.local", 21) == ("nas.example.local", 21)
    assert _recv_all(FakeSocket([b"a", b"b", b""])) == b"ab"


def test_socket_target_and_protocol_helpers_cover_success_and_error_paths():
    assert _parse_socket_target("ftp://nas.example.local", 21, expected_scheme="ftp") == ("nas.example.local", 21)
    assert _parse_socket_target("telnet://switch.example.local:2323", 23, expected_scheme="telnet") == (
        "switch.example.local",
        2323,
    )
    assert _parse_socket_target("nas.example.local:2121", 21) == ("nas.example.local", 2121)
    assert _ftp_parse_pasv("227 Entering Passive Mode (192,0,2,10,4,1)") == ("192.0.2.10", 1025)
    assert _looks_like_ftp_listing("-rw-r--r-- 1 root root 0 Jan 1 file.txt") is True
    assert _looks_like_ftp_listing("garbage") is False
    assert _looks_like_telnet_output("router> ") is True
    assert _looks_like_telnet_output("login incorrect") is False

    with pytest.raises(ValueError, match="expected ftp target"):
        _parse_socket_target("telnet://switch.example.local", 21, expected_scheme="ftp")

    with pytest.raises(ValueError, match="must include a host"):
        _parse_socket_target("", 23)


def test_portable_telnet_runner_retries_and_classifies_transient_socket_failures(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr("vivipi.runtime.checks._sleep_ms", lambda value_ms: sleep_calls.append(value_ms))
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: (_ for _ in ()).throw(OSError(111, "connection refused")))

    result = portable_telnet_runner("telnet://switch.example.local", 10)

    assert result.ok is False
    assert result.details.startswith("refused:")
    assert sleep_calls == [100, 200]


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

    _close_socket(None)
    _close_socket(CloseErrorSocket([]))

    with pytest.raises(ValueError, match="invalid FTP response"):
        _ftp_read_response(FakeSocket([b"oops\r\n"]))

    with pytest.raises(ValueError, match="invalid FTP passive response"):
        _ftp_parse_pasv("227 bad response")


def test_read_until_markers_returns_buffer_when_stream_ends():
    handle = FakeSocket([b"hello", b" world", b""])

    assert _read_until_markers(handle, (b"missing",)) == b"hello world"


def test_portable_ftp_runner_logs_in_and_lists_directory(monkeypatch):
    control_socket = FakeSocket(
        [
            b"220 Ready\r\n",
            b"331 Password required\r\n",
            b"230 Logged in\r\n",
            b"227 Entering Passive Mode (192,0,2,10,4,1)\r\n",
            b"150 Here comes the directory listing\r\n",
            b"226 Transfer complete\r\n",
        ]
    )
    data_socket = FakeSocket([b"-rw-r--r-- 1 root root 0 Jan 1 file.txt\r\n", b""])
    opened = []

    def fake_open_socket(host, port, timeout_s):
        opened.append((host, port, timeout_s))
        return control_socket if len(opened) == 1 else data_socket

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", fake_open_socket)

    result = portable_ftp_runner("ftp://nas.example.local", 10, username="admin", password="secret")

    assert result.ok is True
    assert result.details == "listed 1 entries"
    assert opened == [("nas.example.local", 21, 10), ("192.0.2.10", 1025, 10)]
    assert control_socket.sent == [
        b"USER admin\r\n",
        b"PASS secret\r\n",
        b"PASV\r\n",
        b"LIST\r\n",
        b"QUIT\r\n",
    ]
    assert control_socket.closed is True
    assert data_socket.closed is True


def test_portable_ftp_runner_rejects_invalid_directory_listing(monkeypatch):
    control_socket = FakeSocket(
        [
            b"220 Ready\r\n",
            b"230 Logged in\r\n",
            b"227 Entering Passive Mode (192,0,2,10,4,1)\r\n",
            b"150 Here comes the directory listing\r\n",
            b"226 Transfer complete\r\n",
        ]
    )
    data_socket = FakeSocket([b"???\r\n", b""])
    opened = []

    def fake_open_socket(host, port, timeout_s):
        opened.append((host, port, timeout_s))
        return control_socket if len(opened) == 1 else data_socket

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", fake_open_socket)

    result = portable_ftp_runner("nas.example.local:21", 10)

    assert result.ok is False
    assert result.details == "invalid directory listing"
    assert control_socket.sent == [
        b"USER anonymous\r\n",
        b"PASV\r\n",
        b"LIST\r\n",
    ]


def test_portable_ftp_runner_handles_greeting_login_and_transfer_failures(monkeypatch):
    def run_case(responses, expected_detail):
        control_socket = FakeSocket(responses)
        data_socket = FakeSocket([b"-rw-r--r-- 1 root root 0 Jan 1 file.txt\r\n", b""])
        opened = []

        def fake_open_socket(host, port, timeout_s):
            opened.append((host, port, timeout_s))
            return control_socket if len(opened) == 1 else data_socket

        monkeypatch.setattr("vivipi.runtime.checks._open_socket", fake_open_socket)
        return portable_ftp_runner("ftp://nas.example.local", 10, username="admin", password="secret")

    greeting_failure = run_case([b"421 Down\r\n"], "421 Down")
    login_failure = run_case([b"220 Ready\r\n", b"530 Login incorrect\r\n"], "530 Login incorrect")
    passive_failure = run_case([b"220 Ready\r\n", b"230 Logged in\r\n", b"425 No passive mode\r\n"], "425 No passive mode")
    list_failure = run_case([b"220 Ready\r\n", b"230 Logged in\r\n", b"227 Entering Passive Mode (192,0,2,10,4,1)\r\n", b"450 LIST failed\r\n"], "450 LIST failed")
    transfer_failure = run_case([b"220 Ready\r\n", b"230 Logged in\r\n", b"227 Entering Passive Mode (192,0,2,10,4,1)\r\n", b"150 Opening data\r\n", b"451 Transfer aborted\r\n"], "451 Transfer aborted")

    assert greeting_failure.details == "421 Down"
    assert login_failure.details == "530 Login incorrect"
    assert passive_failure.details == "425 No passive mode"
    assert list_failure.details == "450 LIST failed"
    assert transfer_failure.details == "451 Transfer aborted"


def test_portable_ftp_runner_reports_socket_errors(monkeypatch):
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: (_ for _ in ()).throw(OSError("refused")))

    result = portable_ftp_runner("ftp://nas.example.local", 10)

    assert result.ok is False
    assert result.details == "refused"


def test_portable_telnet_runner_logs_in_and_detects_prompt(monkeypatch):
    handle = FakeSocket([b"login: ", b"Password: ", b"Welcome\r\nrouter> "])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("telnet://switch.example.local", 10, username="ops", password="secret")

    assert result.ok is True
    assert result.details == "session ready"
    assert handle.sent == [b"ops\r\n", b"secret\r\n"]
    assert handle.closed is True


def test_portable_telnet_runner_rejects_login_failure(monkeypatch):
    handle = FakeSocket([b"Login incorrect\r\n"])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10)

    assert result.ok is False
    assert result.details == "login failed"
    assert handle.sent == []


def test_portable_telnet_runner_requests_prompt_and_rejects_invalid_output(monkeypatch):
    handle = FakeSocket([b"   \r\n", b""])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10)

    assert result.ok is False
    assert result.details == "invalid session output"
    assert handle.sent == [b"\r\n"]


def test_portable_telnet_runner_handles_password_failure_and_socket_error(monkeypatch):
    handle = FakeSocket([b"login: ", b"Password: ", b"Access denied\r\n"])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    failed_login = portable_telnet_runner("telnet://switch.example.local", 10, username="ops", password="secret")

    assert failed_login.ok is False
    assert failed_login.details == "login failed"

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: (_ for _ in ()).throw(OSError("refused")))

    socket_failure = portable_telnet_runner("telnet://switch.example.local", 10)

    assert socket_failure.ok is False
    assert socket_failure.details == "refused"