import sys
import builtins
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
    _looks_like_ftp_listing,
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
    calls = []
    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: {"checks": []},
        text="{\"checks\": []}",
        close=lambda: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "urequests",
        SimpleNamespace(
            request=lambda method, target, timeout, headers: (
                calls.append((method, target, timeout, headers)) or fake_response
            )
        ),
    )

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10, password="secret")

    assert result.status_code == 200
    assert result.body == {"checks": []}
    assert calls == [("GET", "http://192.0.2.10:8080/checks", 10, {"Connection": "close", "X-Password": "secret"})]


def test_portable_http_runner_reports_transient_transport_error_without_retry(monkeypatch):
    sleep_calls = []
    attempts = {"count": 0}

    def fake_request(method, target, timeout, headers):
        attempts["count"] += 1
        raise OSError("timed out")

    monkeypatch.setitem(sys.modules, "urequests", SimpleNamespace(request=fake_request))
    monkeypatch.setattr("vivipi.runtime.checks._sleep_ms", lambda value_ms: sleep_calls.append(value_ms))

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.status_code is None
    assert result.details.startswith("timeout")
    assert attempts["count"] == 1
    assert sleep_calls == []


def test_portable_http_runner_falls_back_to_response_text_when_json_parsing_fails(monkeypatch):
    def raise_value_error():
        raise ValueError("bad json")

    calls = []
    fake_response = SimpleNamespace(
        status_code=200,
        json=raise_value_error,
        text="plain text",
        close=lambda: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "urequests",
        SimpleNamespace(
            request=lambda method, target, timeout, headers: (
                calls.append(headers) or fake_response
            )
        ),
    )

    result = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10)

    assert result.body == "plain text"
    assert calls == [{"Connection": "close"}]


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
        assert request.headers["Connection"] == "close"
        assert request.headers["X-password"] == "secret"
        return FakeSuccessResponse('{"checks": []}', 200)

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_success)

    success = portable_http_runner("GET", "http://192.0.2.10:8080/checks", 10, password="secret")

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
    monkeypatch.setitem(
        sys.modules,
        "urequests",
        SimpleNamespace(
            request=lambda method, target, timeout, headers: (_ for _ in ()).throw(OSError("network is unreachable"))
        ),
    )
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
    fake_time = SimpleNamespace(sleep_calls=[], sleep_ms=lambda value_ms: fake_time.sleep_calls.append(value_ms))
    monkeypatch.setattr(runtime_checks, "time", fake_time)

    _sleep_ms(0)
    _sleep_ms(5)

    assert fake_time.sleep_calls == [5]
    assert _normalize_error_text(RuntimeError()) == "RuntimeError"
    assert _classify_network_error(TimeoutError("timed out")) == "timeout"
    assert _classify_network_error(OSError(-2, "name resolution failed")) == "dns"
    assert _classify_network_error(OSError(111, "connection refused")) == "refused"
    assert _classify_network_error(OSError(101, "network unreachable")) == "network"
    assert _classify_network_error(OSError("broken pipe")) == "reset"
    assert _format_network_error(OSError("broken pipe")).startswith("reset:")
    assert _runtime_optional_auth(None) is None
    assert _runtime_optional_auth("  admin  ") == "admin"
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


def test_portable_telnet_runner_classifies_transient_socket_failures_without_retry(monkeypatch):
    sleep_calls = []
    attempts = {"count": 0}
    monkeypatch.setattr("vivipi.runtime.checks._sleep_ms", lambda value_ms: sleep_calls.append(value_ms))

    def fake_open_socket(host, port, timeout_s):
        attempts["count"] += 1
        raise OSError(111, "connection refused")

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", fake_open_socket)

    result = portable_telnet_runner("telnet://switch.example.local", 10)

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

    _close_socket(None)
    _close_socket(CloseErrorSocket([]))

    with pytest.raises(ValueError, match="invalid FTP response"):
        _ftp_read_response(FakeSocket([b"oops\r\n"]))

    with pytest.raises(ValueError, match="invalid FTP passive response"):
        _ftp_parse_pasv("227 bad response")


def test_read_until_markers_returns_buffer_when_stream_ends():
    handle = FakeSocket([b"hello", b" world", b""])

    assert _read_until_markers(handle, (b"missing",)) == b"hello world"


def test_portable_ftp_runner_accepts_valid_greeting_and_quits_cleanly(monkeypatch):
    control_socket = FakeSocket(
        [
            b"220 Ready\r\n",
            b"221 Goodbye\r\n",
        ]
    )
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: control_socket)

    result = portable_ftp_runner("ftp://nas.example.local", 10, username="admin", password="secret")

    assert result.ok is True
    assert result.details == "ftp greeting ready"
    assert control_socket.sent == [
        b"QUIT\r\n",
    ]
    assert control_socket.closed is True


def test_portable_ftp_runner_rejects_invalid_greeting(monkeypatch):
    control_socket = FakeSocket([b"500 Down\r\n"])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: control_socket)

    result = portable_ftp_runner("nas.example.local:21", 10)

    assert result.ok is False
    assert result.details == "500 Down"
    assert control_socket.sent == []


def test_portable_ftp_runner_reports_greeting_failures(monkeypatch):
    def run_case(responses, expected_detail):
        control_socket = FakeSocket(responses)
        monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: control_socket)
        return portable_ftp_runner("ftp://nas.example.local", 10, username="admin", password="secret")

    greeting_failure = run_case([b"421 Down\r\n"], "421 Down")

    assert greeting_failure.details == "421 Down"


def test_portable_ftp_runner_reports_socket_errors(monkeypatch):
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: (_ for _ in ()).throw(OSError("refused")))

    result = portable_ftp_runner("ftp://nas.example.local", 10)

    assert result.ok is False
    assert result.details == "refused"


def test_portable_telnet_runner_accepts_banner_output(monkeypatch):
    handle = FakeSocket([b"login: ", b"Password: ", b"Welcome\r\nrouter> "])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("telnet://switch.example.local", 10, username="ops", password="secret")

    assert result.ok is True
    assert result.details == "banner ready"
    assert handle.sent == []
    assert handle.closed is True


def test_portable_telnet_runner_rejects_login_failure(monkeypatch):
    handle = FakeSocket([b"Login incorrect\r\n"])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10)

    assert result.ok is False
    assert result.details == "login failed"
    assert handle.sent == []


def test_portable_telnet_runner_accepts_blank_sessions_as_connected(monkeypatch):
    handle = FakeSocket([b"   \r\n", b""])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10)

    assert result.ok is True
    assert result.details == "connected"
    assert handle.sent == []


def test_portable_telnet_runner_treats_delayed_output_as_connected(monkeypatch):
    class TimeoutThenResponseSocket(FakeSocket):
        def __init__(self):
            super().__init__([])
            self.recv_calls = 0

        def recv(self, _size):
            self.recv_calls += 1
            if self.recv_calls == 1:
                raise OSError("timed out")
            if self.recv_calls == 2:
                return b"\xff\xfb\x01READY\r\n"
            return b""

    handle = TimeoutThenResponseSocket()
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10)

    assert result.ok is True
    assert result.details == "connected"
    assert handle.sent == []


def test_portable_telnet_runner_accepts_password_prompt_as_banner(monkeypatch):
    handle = FakeSocket([b"Password: \xff\xfb\x01", b"\r\nREADY\r\n"])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    result = portable_telnet_runner("switch.example.local:23", 10, password="secret")

    assert result.ok is True
    assert result.details == "banner ready"
    assert handle.sent == [bytes((255, 254, 1))]


def test_portable_telnet_runner_handles_explicit_failure_text_and_socket_error(monkeypatch):
    handle = FakeSocket([b"Access denied\r\n"])
    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: handle)

    failed_login = portable_telnet_runner("telnet://switch.example.local", 10)

    assert failed_login.ok is False
    assert failed_login.details == "login failed"

    monkeypatch.setattr("vivipi.runtime.checks._open_socket", lambda host, port, timeout_s: (_ for _ in ()).throw(OSError("refused")))

    socket_failure = portable_telnet_runner("telnet://switch.example.local", 10)

    assert socket_failure.ok is False
    assert socket_failure.details == "refused"
