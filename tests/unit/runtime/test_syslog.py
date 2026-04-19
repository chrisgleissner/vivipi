import pytest

import vivipi.runtime.syslog as syslog_module
from vivipi.core.models import CheckDefinition, CheckType
from vivipi.runtime.syslog import UdpSyslogSink, build_syslog_sink, resolve_syslog_config


class FakeSocket:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.sent = []
        self.closed = False
        self.blocking = None

    def setblocking(self, value):
        self.blocking = value

    def sendto(self, payload, address):
        if self.should_fail:
            raise OSError("network unreachable")
        self.sent.append((payload, address))

    def close(self):
        self.closed = True


class FakeSocketModule:
    SOCK_DGRAM = 2

    def __init__(self, sock):
        self._sock = sock
        self.created = []

    def getaddrinfo(self, host, port, family, socktype):
        return [(2, socktype, 17, "", (host, port))]

    def socket(self, family, socktype, proto):
        self.created.append((family, socktype, proto))
        return self._sock


class TimeoutOnlySocket:
    def __init__(self):
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def sendto(self, payload, address):
        self.sent.append((payload, address))

    def close(self):
        self.closed = True


def test_resolve_syslog_config_defaults_to_service_base_url_host_and_port():
    config = {
        "wifi": {"host_aliases": {"svc": "192.0.2.9"}},
        "service": {"base_url": "http://svc:8080/checks"},
    }

    resolved = resolve_syslog_config(config)

    assert resolved == {
        "enabled": True,
        "host": "192.0.2.9",
        "port": 514,
        "retry_interval_s": 5.0,
    }


def test_build_syslog_sink_falls_back_to_http_definition_host_when_service_base_url_is_missing():
    config = {"service": {}, "wifi": {"host_aliases": {"logger": "192.0.2.5"}}}
    definitions = (
        CheckDefinition(identifier="adb", name="ADB", check_type=CheckType.HTTP, target="http://logger:8081/vivipi/probe/adb"),
    )

    sink = build_syslog_sink(config, definitions=definitions, socket_module=FakeSocketModule(FakeSocket()), now_provider=lambda: 0.0)

    assert sink is not None
    assert sink.host == "192.0.2.5"
    assert sink.port == 514


def test_resolve_syslog_config_resolves_explicit_host_aliases_through_wifi_aliases():
    config = {
        "wifi": {"host_aliases": {"mickey": "192.0.2.42"}},
        "service": {"syslog": {"host": "mickey", "port": 514}},
    }

    resolved = resolve_syslog_config(config)

    assert resolved["host"] == "192.0.2.42"
    assert resolved["enabled"] is True


def test_syslog_helpers_cover_bool_parsing_host_parsing_and_validation_errors():
    assert syslog_module._coerce_bool("off", True) is False
    assert syslog_module._extract_host_port(None) == (None, None)
    assert syslog_module._extract_host_port("  ") == (None, None)
    assert syslog_module._extract_host_port("logger:1514") == ("logger", 1514)
    assert syslog_module._extract_host_port("logger") == ("logger", None)
    assert syslog_module._resolve_host_alias(" logger ", {"logger": "192.0.2.8"}) == "192.0.2.8"
    assert syslog_module._resolve_host_alias("logger", {"logger": "   "}) == "logger"

    with pytest.raises(ValueError, match="service.syslog.enabled must be a boolean"):
        syslog_module._coerce_bool("maybe", True)

    assert resolve_syslog_config({"service": {"syslog": {"enabled": False}}}) == {
        "enabled": False,
        "host": None,
        "port": 514,
        "retry_interval_s": 5.0,
    }

    with pytest.raises(ValueError, match="service.syslog.port"):
        resolve_syslog_config({"service": {"syslog": {"host": "logger", "port": 70000}}})

    with pytest.raises(ValueError, match="service.syslog.retry_interval_s"):
        resolve_syslog_config({"service": {"syslog": {"host": "logger", "retry_interval_s": -1}}})

    assert build_syslog_sink({"service": {"syslog": {"enabled": True}}}) is None


def test_udp_syslog_sink_emits_once_and_warns_only_on_first_failure():
    sink = UdpSyslogSink("192.0.2.10", socket_module=FakeSocketModule(FakeSocket(should_fail=True)), now_provider=lambda: 10.0)

    warning = sink.emit("[vivipi] [INFO][CORE] boot")
    second_warning = sink.emit("[vivipi] [INFO][CORE] boot")

    assert warning is not None
    assert warning.startswith("[vivipi] [WARN][SYSLOG] unavailable")
    assert second_warning is None


def test_udp_syslog_sink_sends_nonblocking_udp_payloads_when_available():
    sock = FakeSocket()
    socket_module = FakeSocketModule(sock)
    sink = UdpSyslogSink("192.0.2.10", port=514, socket_module=socket_module, now_provider=lambda: 0.0)

    warning = sink.emit("[vivipi] [INFO][CHECK] run id=router")

    assert warning is None
    assert sock.blocking is False
    assert socket_module.created == [(2, socket_module.SOCK_DGRAM, 17)]
    assert sock.sent == [(b"[vivipi] [INFO][CHECK] run id=router", ("192.0.2.10", 514))]


def test_udp_syslog_sink_falls_back_to_settimeout_when_setblocking_is_unavailable():
    sock = TimeoutOnlySocket()
    socket_module = FakeSocketModule(sock)
    sink = UdpSyslogSink("192.0.2.10", port=514, socket_module=socket_module, now_provider=lambda: 0.0)

    warning = sink.emit("[vivipi] [INFO][CHECK] run id=router")

    assert warning is None
    assert sock.timeout == 0
    assert sock.sent == [(b"[vivipi] [INFO][CHECK] run id=router", ("192.0.2.10", 514))]


def test_udp_syslog_sink_retries_after_backoff_and_recovers():
    class SequenceSocketModule:
        SOCK_DGRAM = 2

        def __init__(self):
            self.issued = []
            self.created = []

        def getaddrinfo(self, host, port, family, socktype):
            return [(2, socktype, 17, "", (host, port))]

        def socket(self, family, socktype, proto):
            sock = FakeSocket(should_fail=not self.issued)
            self.issued.append(sock)
            self.created.append((family, socktype, proto))
            return sock

    now_values = iter((0.0, 1.0, 6.0))
    socket_module = SequenceSocketModule()
    sink = UdpSyslogSink("192.0.2.10", port=514, socket_module=socket_module, now_provider=lambda: next(now_values))

    assert sink.emit("[vivipi] [INFO][CORE] boot") is not None
    assert sink.emit("[vivipi] [INFO][CORE] boot") is None
    assert sink.emit("[vivipi] [INFO][CORE] boot") is None

    assert socket_module.issued[0].closed is True
    assert socket_module.issued[1].sent == [(b"[vivipi] [INFO][CORE] boot", ("192.0.2.10", 514))]


def test_udp_syslog_sink_forces_datagram_socket_when_lookup_reports_stream_type():
    sock = FakeSocket()

    class QuirkySocketModule(FakeSocketModule):
        def getaddrinfo(self, host, port, family, socktype):
            return [(2, 1, 0, "", (host, port))]

    socket_module = QuirkySocketModule(sock)
    sink = UdpSyslogSink("192.0.2.10", port=514, socket_module=socket_module, now_provider=lambda: 0.0)

    warning = sink.emit("[vivipi] [INFO][CHECK] run id=router")

    assert warning is None
    assert socket_module.created == [(2, socket_module.SOCK_DGRAM, 0)]
    assert sock.sent == [(b"[vivipi] [INFO][CHECK] run id=router", ("192.0.2.10", 514))]