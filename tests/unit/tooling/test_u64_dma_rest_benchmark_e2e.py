"""End-to-end benchmark tests against in-process mock U64 listeners.

These tests run the *real* benchmark (`main()`/executors) against loopback mock
servers that speak the exact 1541Ultimate wire protocols verified from firmware
source:

  - socket-DMA (TCP/64): ``<u16-le cmd><u16-le len><payload>`` frames.
    DMAWRITE (0xFF06) payload is ``<u16-le addr> + data`` and gets NO response;
    a zero-length DEBUG_REG (0xFF76) returns exactly one byte and is the
    same-socket completion barrier; AUTHENTICATE (0xFF1F) returns one byte;
    IDENTIFY (0xFF0E) returns ``<len><title>``. The server is strictly
    sequential per connection, mirroring the firmware's single dmaThread.
    (software/network/socket_dma.cc:103-161,287-294,453-489)
  - REST writemem: PUT ``/v1/machine:writemem?address=XXXX&data=HEX`` and POST
    ``/v1/machine:writemem?address=XXXX`` with a raw octet-stream body.
    (software/api/route_machine.cc:71-163)

Unlike the unit-level tests (which fake the socket/HTTP objects), these drive
the whole tool through ``main()`` and assert it produces the *expected numbers*
and the *expected bytes on the wire*. This is the regression guard that the
earlier test suite lacked: it would have caught the latency-unit bug (latencies
stored in seconds under an ``_ms`` name) and any DMA/REST framing error, because
the mock listeners decode every frame the way the firmware does and the test
checks request counts, payload bytes, addresses, and that latency is reported in
milliseconds.

All servers bind to 127.0.0.1:0 (ephemeral), run in daemon threads, and are torn
down in ``finally``. No real hardware, no fixed ports, deterministic payloads.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests.unit.tooling._script_loader import load_script_module


def load_module():
    return load_script_module("u64_dma_rest_benchmark")


# --------------------------------------------------------------------------- #
# Mock socket-DMA listener (TCP/64), protocol-accurate to the firmware
# --------------------------------------------------------------------------- #


class MockDmaServer:
    """Loopback TCP server implementing the U64 socket-DMA command protocol."""

    CMD_DMAWRITE = 0xFF06
    CMD_REUWRITE = 0xFF07
    CMD_IDENTIFY = 0xFF0E
    CMD_AUTHENTICATE = 0xFF1F
    CMD_DEBUG_REG = 0xFF76

    def __init__(self, password: str | None = None):
        self.password = password
        self.writes: list[tuple[int, bytes]] = []  # (address/offset, data)
        self.frames: list[tuple[int, bytes]] = []  # (command, raw payload)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "MockDmaServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=3)

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _serve(self) -> None:
        self._sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(3.0)
                self._handle_connection(conn)

    def _handle_connection(self, conn: socket.socket) -> None:
        authenticated = self.password is None
        try:
            while not self._stop.is_set():
                header = self._recv_exact(conn, 4)
                if header is None:
                    return
                cmd = header[0] | (header[1] << 8)
                length = header[2] | (header[3] << 8)
                payload = b"" if length == 0 else self._recv_exact(conn, length)
                if payload is None:
                    return
                self.frames.append((cmd, payload))
                if cmd == self.CMD_AUTHENTICATE:
                    ok = self.password is not None and payload == self.password.encode("utf-8")
                    authenticated = ok or self.password is None
                    conn.sendall(b"\x01" if ok else b"\x00")
                    if not ok:
                        return  # firmware disconnects on failed auth
                elif not authenticated:
                    return  # firmware disconnects on pre-auth command
                elif cmd == self.CMD_DMAWRITE:
                    address = payload[0] | (payload[1] << 8)
                    self.writes.append((address, payload[2:]))
                    # no response, exactly like the firmware
                elif cmd == self.CMD_REUWRITE:
                    offset = payload[0] | (payload[1] << 8) | (payload[2] << 16)
                    self.writes.append((offset, payload[3:]))
                    # no response
                elif cmd == self.CMD_DEBUG_REG:
                    conn.sendall(b"\x00")  # one-byte read-only barrier
                elif cmd == self.CMD_IDENTIFY:
                    title = b"MOCK-U64"
                    conn.sendall(bytes([len(title)]) + title)
                else:
                    return
        except (socket.timeout, OSError):
            return


# --------------------------------------------------------------------------- #
# Mock REST writemem listener (HTTP)
# --------------------------------------------------------------------------- #


class _WritememHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silence test output
        pass

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": method,
                "path": self.path,
                "route": parsed.path,
                "address": (query.get("address") or [None])[0],
                "data_hex": (query.get("data") or [None])[0],
                "body": body,
                "content_type": self.headers.get("Content-Type"),
                "x_password": self.headers.get("X-Password"),
            }
        )
        payload = b'{"errors":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")  # firmware has no keep-alive
        self.end_headers()
        self.wfile.write(payload)

    def do_PUT(self) -> None:  # noqa: N802 (http.server naming)
        self._handle("PUT")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")


class MockRestServer:
    def __init__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _WritememHandler)
        self._server.requests = []  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def requests(self) -> list[dict]:
        return self._server.requests  # type: ignore[attr-defined,no-any-return]

    def __enter__(self) -> "MockRestServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run_main(module, argv: list[str], capsys) -> tuple[int, list[dict]]:
    rc = module.main(argv)
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.strip()]
    return rc, events


def _summary_event(events: list[dict]) -> dict:
    return next(e for e in events if e.get("run") == "end")


def _expected_payloads(module, profile_name: str, units: int) -> list[tuple[int, bytes]]:
    """Deterministic (address, payload) the benchmark must put on the wire."""
    config = module.load_traffic_config(module.DEFAULT_TRAFFIC_CONFIG)
    profile = config.select(profile_name)
    out: list[tuple[int, bytes]] = []
    for unit_index in range(units):
        for lw in module.expand_unit(profile, unit_index):
            payload = module.generate_payload(
                traffic_name=profile.name,
                unit_index=unit_index,
                emitted_write_index=lw.emitted_write_index,
                space=lw.space,
                address=lw.address,
                byte_count=lw.bytes_count,
                dirty_offset=lw.dirty_offset,
                seed=profile.seed,
                pattern=profile.payload_pattern,
            )
            out.append((lw.address, payload))
    return out


# --------------------------------------------------------------------------- #
# DMA end-to-end
# --------------------------------------------------------------------------- #


def test_dma_end_to_end_counts_bytes_framing_and_payloads(capsys):
    module = load_module()
    iterations = 4
    with MockDmaServer() as server:
        rc, events = _run_main(
            module,
            [
                "-H", "127.0.0.1",
                "--dma-port", str(server.port),
                "--probes", "dma",
                "--traffic", "single-write",
                "--iterations", str(iterations),
                "--dma-ack-mode", "barrier",
                "--dma-connection", "persistent",
            ],
            capsys,
        )

    assert rc == 0
    summary = _summary_event(events)
    assert summary["ok"] is True
    dma = summary["summary"]["dma"]
    assert dma["requests"] == iterations
    assert dma["failed_requests"] == 0
    assert dma["request_bytes"] == iterations * 64

    # Framing correctness: the listener decoded exactly `iterations` DMAWRITE
    # frames, each addressed to $0400 with a 64-byte payload.
    assert len(server.writes) == iterations
    for address, data in server.writes:
        assert address == 0x0400
        assert len(data) == 64

    # Deterministic payloads: bytes on the wire match generate_payload exactly.
    expected = _expected_payloads(module, "single-write", iterations)
    assert server.writes == expected

    # Barrier discipline: one zero-length DEBUG_REG per write, and DMAWRITE
    # frames carry NO response (the listener never replies to 0xFF06).
    barriers = [f for f in server.frames if f[0] == MockDmaServer.CMD_DEBUG_REG]
    assert len(barriers) == iterations
    assert all(payload == b"" for _, payload in barriers)


def test_dma_request_events_report_latency_in_milliseconds(capsys):
    """A request event's latency must be plausible *milliseconds*, never seconds.

    The earlier benchmark stored perf_counter() deltas (seconds) in latency_ms,
    making every latency 1000x too small. Even loopback DMA takes well over the
    seconds-scale value such a bug would produce; this asserts the field is
    populated and in a sane millisecond band end to end.
    """
    module = load_module()
    with MockDmaServer() as server:
        _, events = _run_main(
            module,
            [
                "-H", "127.0.0.1",
                "--dma-port", str(server.port),
                "--probes", "dma",
                "--traffic", "single-write",
                "--iterations", "5",
                "--dma-ack-mode", "barrier",
            ],
            capsys,
        )
    requests = [e for e in events if e.get("event") == "request" and e.get("phase_probe") == "dma"]
    assert len(requests) == 5
    for event in requests:
        assert event["ok"] is True
        assert "latency_ms" in event
        # Loopback round-trips are sub-millisecond to a few ms; bound loosely but
        # far below any seconds-scale value and far above the 1000x-too-small bug.
        assert 0.0 <= event["latency_ms"] < 5000.0


# --------------------------------------------------------------------------- #
# REST end-to-end
# --------------------------------------------------------------------------- #


def test_rest_put_end_to_end_counts_method_path_and_hex(capsys):
    module = load_module()
    iterations = 4
    with MockRestServer() as server:
        rc, events = _run_main(
            module,
            [
                "-H", "127.0.0.1",
                "--http-port", str(server.port),
                "--probes", "rest",
                "--traffic", "single-write",
                "--iterations", str(iterations),
                "--rest-method", "put",
            ],
            capsys,
        )

    assert rc == 0
    rest = _summary_event(events)["summary"]["rest"]
    assert rest["requests"] == iterations
    assert rest["failed_requests"] == 0
    assert rest["request_bytes"] == iterations * 64

    reqs = server.requests
    assert len(reqs) == iterations
    expected = _expected_payloads(module, "single-write", iterations)
    for req, (address, payload) in zip(reqs, expected):
        assert req["method"] == "PUT"
        assert req["route"] == "/v1/machine:writemem"
        assert req["address"] == f"{address:04X}"
        assert bytes.fromhex(req["data_hex"]) == payload
        assert req["body"] == b""  # PUT carries data in the query string


def test_rest_post_end_to_end_sends_octet_stream_body(capsys):
    module = load_module()
    iterations = 3
    with MockRestServer() as server:
        rc, events = _run_main(
            module,
            [
                "-H", "127.0.0.1",
                "--http-port", str(server.port),
                "--probes", "rest",
                "--traffic", "single-write",
                "--iterations", str(iterations),
                "--rest-method", "post",
            ],
            capsys,
        )

    assert rc == 0
    rest = _summary_event(events)["summary"]["rest"]
    assert rest["requests"] == iterations
    assert rest["request_bytes"] == iterations * 64

    reqs = server.requests
    assert len(reqs) == iterations
    expected = _expected_payloads(module, "single-write", iterations)
    for req, (address, payload) in zip(reqs, expected):
        assert req["method"] == "POST"
        assert req["address"] == f"{address:04X}"
        assert req["content_type"] == "application/octet-stream"
        assert req["body"] == payload  # raw bytes in the body, not hex
        assert req["data_hex"] is None


def test_rest_sends_x_password_header_end_to_end(capsys):
    module = load_module()
    with MockRestServer() as server:
        _run_main(
            module,
            [
                "-H", "127.0.0.1",
                "--http-port", str(server.port),
                "--probes", "rest",
                "--traffic", "single-write",
                "--iterations", "2",
                "--rest-method", "put",
                "--network-password", "hunter2",
            ],
            capsys,
        )
    assert server.requests
    assert all(req["x_password"] == "hunter2" for req in server.requests)


# --------------------------------------------------------------------------- #
# Full sequential REST-then-DMA run against both mocks
# --------------------------------------------------------------------------- #


def test_full_sequential_run_against_both_mock_listeners(capsys):
    module = load_module()
    iterations = 3
    with MockRestServer() as rest_server, MockDmaServer() as dma_server:
        rc, events = _run_main(
            module,
            [
                "-H", "127.0.0.1",
                "--http-port", str(rest_server.port),
                "--dma-port", str(dma_server.port),
                "--traffic", "single-write",
                "--iterations", str(iterations),
                "--probes", "rest,dma",
            ],
            capsys,
        )

    assert rc == 0

    # Every stdout line was valid JSON (the loop above would have raised otherwise);
    # assert the documented event taxonomy is present.
    assert any(e.get("run") == "start" for e in events)
    request_events = [e for e in events if e.get("event") == "request"]
    assert request_events
    assert all("request_bytes" in e for e in request_events)
    assert {e["phase_probe"] for e in request_events} == {"rest", "dma"}

    summary = _summary_event(events)
    assert summary["ok"] is True
    assert summary["summary"]["rest"]["requests"] == iterations
    assert summary["summary"]["dma"]["requests"] == iterations
    assert summary["summary"]["rest"]["request_bytes"] == iterations * 64
    assert summary["summary"]["dma"]["request_bytes"] == iterations * 64

    # Both transports put identical payload bytes on the wire for the same units.
    expected = _expected_payloads(module, "single-write", iterations)
    assert dma_server.writes == expected
    rest_payloads = [
        (int(req["address"], 16), bytes.fromhex(req["data_hex"]))
        for req in rest_server.requests
    ]
    assert rest_payloads == expected


# --------------------------------------------------------------------------- #
# Deterministic latency-unit regression guard (no network, fake clock)
# --------------------------------------------------------------------------- #


class _FakeSocket:
    def __init__(self, scripted_responses=None):
        self.sent = bytearray()
        self._scripted = list(scripted_responses or [])
        self._index = 0

    def settimeout(self, timeout):
        pass

    def setsockopt(self, *args):
        pass

    def sendall(self, data):
        self.sent.extend(data)

    def recv(self, size):
        if self._index < len(self._scripted):
            chunk = self._scripted[self._index]
            self._index += 1
            return chunk[:size]
        return b""

    def close(self):
        pass


class _FakeHttpResponse:
    status = 200

    def read(self):
        return b'{"errors":[]}'


class _FakeHttpConnection:
    def __init__(self, *args, **kwargs):
        self.requests = []

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, dict(headers or {})))

    def getresponse(self):
        return _FakeHttpResponse()

    def close(self):
        pass


def _single_write_profile(module):
    return module.load_traffic_config(module.DEFAULT_TRAFFIC_CONFIG).select("single-write")


def _ctx_with_fake_clock(module, profile, *, step_s: float):
    """Context whose perf clock advances by exactly `step_s` on every call."""
    args = module.build_parser().parse_args(["-H", "127.0.0.1"])
    ctx = module.build_context(args, profile, emit=lambda e: None)
    state = {"t": 0.0}

    def perf() -> float:
        state["t"] += step_s
        return state["t"]

    ctx.perf_fn = perf
    return ctx


def test_dma_executor_latency_is_milliseconds_with_fake_clock():
    module = load_module()
    profile = _single_write_profile(module)
    ctx = _ctx_with_fake_clock(module, profile, step_s=0.25)  # 250 ms per perf tick
    ctx.dma_connection = module.DMA_CONN_PER_REQUEST
    ctx.dma_ack_mode = module.DMA_ACK_BARRIER
    ctx.socket_factory = lambda addr, timeout: _FakeSocket(scripted_responses=[b"\x00"])

    executor = module.DmaExecutor(ctx)
    logical = module.expand_unit(profile, 0)[0]
    extra = executor.execute(logical, b"\x00" * logical.bytes_count)

    # execute() reads perf at t0 then once more for the delta: one 0.25 s tick.
    # Correct code reports 250.0 ms; the seconds-bug would report 0.25.
    assert extra["latency_ms"] == 250.0


def test_rest_executor_latency_is_milliseconds_with_fake_clock():
    module = load_module()
    profile = _single_write_profile(module)
    ctx = _ctx_with_fake_clock(module, profile, step_s=0.25)
    ctx.http_connection_factory = lambda *a, **kw: _FakeHttpConnection()

    executor = module.RestExecutor(ctx)
    logical = module.expand_unit(profile, 0)[0]
    extra = executor.execute(logical, b"\x00" * logical.bytes_count)

    assert extra["latency_ms"] == 250.0
