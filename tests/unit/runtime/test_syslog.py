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