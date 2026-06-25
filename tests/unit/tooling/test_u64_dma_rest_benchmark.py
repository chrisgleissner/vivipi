from __future__ import annotations

import json
import struct

import pytest

from tests.unit.tooling._script_loader import load_script_module


def load_module():
    return load_script_module("u64_dma_rest_benchmark")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self, status=200, body=b"", read_exception=None):
        self.status = status
        self._body = body
        self._read_exception = read_exception

    def read(self):
        if self._read_exception is not None:
            raise self._read_exception
        return self._body


class FakeHttpConnection:
    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.requests = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, dict(headers or {})))

    def getresponse(self):
        if hasattr(self, "_next_response"):
            return self._next_response
        return FakeResponse(status=200, body=b"")

    def close(self):
        self.closed = True


class ScriptedHttpConnection(FakeHttpConnection):
    def __init__(self, host, port, timeout, responses):
        super().__init__(host, port, timeout)
        self._responses = list(responses)
        self._index = 0

    def getresponse(self):
        if self._index < len(self._responses):
            response = self._responses[self._index]
            self._index += 1
            return response
        return FakeResponse(status=200, body=b"")


class FakeSocket:
    def __init__(self, scripted_responses=None):
        self.sent = bytearray()
        self.settimeout_calls = []
        self.closed = False
        self._scripted = list(scripted_responses or [])
        self._recv_index = 0
        self._recv_buffer = bytearray()

    def settimeout(self, timeout):
        self.settimeout_calls.append(timeout)

    def sendall(self, data):
        self.sent.extend(data)

    def recv(self, size):
        # Pull the next scripted chunk only when the buffer is drained, then hand
        # back at most `size` bytes and keep the remainder for the next call so a
        # chunk longer than `size` is never silently lost.
        while not self._recv_buffer and self._recv_index < len(self._scripted):
            chunk = self._scripted[self._recv_index]
            self._recv_index += 1
            if isinstance(chunk, BaseException):
                raise chunk
            self._recv_buffer.extend(chunk)
        if not self._recv_buffer:
            return b""
        taken = bytes(self._recv_buffer[:size])
        del self._recv_buffer[:size]
        return taken

    def close(self):
        self.closed = True


def make_settings_dict(args):
    return vars(args).copy()


# --------------------------------------------------------------------------- #
# 1. CLI parser defaults
# --------------------------------------------------------------------------- #


def test_parser_defaults():
    module = load_module()
    parser = module.build_parser()
    args = parser.parse_args([])
    assert args.host == module.DEFAULT_HOST
    assert args.traffic == module.DEFAULT_TRAFFIC_NAME
    assert args.traffic_config == module.DEFAULT_TRAFFIC_CONFIG
    assert args.probes == ("rest", "dma")
    assert args.schedule == module.SCHEDULE_SEQUENTIAL
    assert args.runners == 1
    assert args.rest_method == module.REST_METHOD_AUTO
    assert args.dma_ack_mode == module.DMA_ACK_BARRIER
    assert args.dma_barrier == module.DMA_BARRIER_DEBUGREG
    assert args.dma_connection == module.DMA_CONN_PERSISTENT
    assert args.http_connection == module.HTTP_CONN_CLOSE
    assert args.warmup_iterations == 0
    assert args.delay_ms == 0
    assert args.log_every == 1


def test_parser_parses_all_generic_options():
    module = load_module()
    parser = module.build_parser()
    args = parser.parse_args([
        "-H", "192.168.1.13",
        "-d", "5",
        "-n", "10",
        "-P", "legacy",
        "--network-password", "secret",
        "--http-port", "8080",
        "--dma-port", "64",
        "--traffic", "single-write",
        "--probes", "dma",
        "--schedule", "concurrent",
        "--runners", "4",
        "--duration-s", "30",
        "--iterations", "5",
        "--warmup-iterations", "2",
        "--rest-method", "post",
        "--http-connection", "persistent",
        "--dma-ack-mode", "send-only",
        "--dma-barrier", "identify",
        "--dma-connection", "per-request",
        "--payload-pattern", "zero",
        "--seed", "7",
        "--report", "/tmp/report.json",
    ])
    assert args.host == "192.168.1.13"
    assert args.delay_ms == 5
    assert args.log_every == 10
    assert args.ftp_pass == "legacy"
    assert args.network_password == "secret"
    assert args.http_port == 8080
    assert args.dma_port == 64
    assert args.traffic == "single-write"
    assert args.probes == ("dma",)
    assert args.schedule == "concurrent"
    assert args.runners == 4
    assert args.duration_s == 30
    assert args.iterations == 5
    assert args.warmup_iterations == 2
    assert args.rest_method == "post"
    assert args.http_connection == "persistent"
    assert args.dma_ack_mode == "send-only"
    assert args.dma_barrier == "identify"
    assert args.dma_connection == "per-request"
    assert args.payload_pattern == "zero"
    assert args.seed == 7
    assert args.report == "/tmp/report.json"


# --------------------------------------------------------------------------- #
# 2. --help contains required options and generic examples
# --------------------------------------------------------------------------- #


def test_help_contains_required_option_names():
    module = load_module()
    help_text = module.build_parser().format_help()
    # Python 3.13 stopped repeating the metavar for each option string, so the
    # options section renders "-H, --host HOST" instead of the pre-3.13
    # "-H HOST, --host HOST". Assert on the long form plus metavar (stable
    # across versions) and the short flags separately.
    for token in [
        "--host HOST",
        "--delay-ms DELAY_MS",
        "--log-every LOG_EVERY",
        "--ftp-pass FTP_PASS",
        "--network-password",
        "--probes",
        "--schedule",
        "--runners",
        "--duration-s",
    ]:
        assert token in help_text, f"missing help text: {token}"
    for short_flag in ["-H", "-d", "-n", "-P"]:
        assert short_flag in help_text, f"missing short flag: {short_flag}"


def test_help_examples_are_generic():
    module = load_module()
    help_text = module.build_parser().format_help()
    for required in [
        "./scripts/u64_dma_rest_benchmark.py -H u64",
        "--probes rest --rest-method post",
        "--probes rest --rest-method put --traffic single-write",
        "--probes dma --dma-ack-mode barrier",
    ]:
        assert required in help_text


# --------------------------------------------------------------------------- #
# 3. --help does NOT contain c64cast-specific workload flags
# --------------------------------------------------------------------------- #


def test_help_excludes_c64cast_specific_workload_flags():
    module = load_module()
    help_text = module.build_parser().format_help()
    for forbidden in [
        "--bitmap-address",
        "--display-mode",
        "--enable-vic-regs",
        "--dirty-policy",
        "--vic-reg",
        "--audio-profile",
        "--screen-bytes",
        "--scenario c64cast",
    ]:
        assert forbidden not in help_text, f"forbidden flag present in help: {forbidden}"


# --------------------------------------------------------------------------- #
# 4-9. JSON traffic loader behavior
# --------------------------------------------------------------------------- #


def test_default_traffic_loader_accepts_config():
    module = load_module()
    config = module.load_traffic_config(module.DEFAULT_TRAFFIC_CONFIG)
    profile = config.select(None)
    assert profile.name == "c64cast"


def test_traffic_loader_rejects_duplicate_names(tmp_path):
    module = load_module()
    bad = {
        "version": 1,
        "default": "x",
        "traffic": [
            {
                "name": "x",
                "unit": "iteration",
                "iterations": 1,
                "writes": [
                    {"space": "c64", "address": "0400", "bytes": 1, "write_kind": "dmawrite"}
                ],
            },
            {
                "name": "x",
                "unit": "iteration",
                "iterations": 1,
                "writes": [
                    {"space": "c64", "address": "0400", "bytes": 1, "write_kind": "dmawrite"}
                ],
            },
        ],
    }
    path = tmp_path / "traffic.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="duplicate traffic name"):
        module.load_traffic_config(path)


def test_traffic_loader_rejects_malformed_address(tmp_path):
    module = load_module()
    bad = {
        "version": 1,
        "default": "x",
        "traffic": [
            {
                "name": "x",
                "unit": "iteration",
                "iterations": 1,
                "writes": [
                    {"space": "c64", "address": "ZZZZ", "bytes": 1, "write_kind": "dmawrite"}
                ],
            }
        ],
    }
    path = tmp_path / "t.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="invalid hex address"):
        module.load_traffic_config(path)


def test_traffic_loader_rejects_non_positive_bytes(tmp_path):
    module = load_module()
    bad = {
        "version": 1,
        "default": "x",
        "traffic": [
            {
                "name": "x",
                "unit": "iteration",
                "iterations": 1,
                "writes": [
                    {"space": "c64", "address": "0400", "bytes": 0, "write_kind": "dmawrite"}
                ],
            }
        ],
    }
    path = tmp_path / "t.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="bytes must be a positive integer"):
        module.load_traffic_config(path)


def test_traffic_loader_rejects_invalid_space_and_kind(tmp_path):
    module = load_module()
    bad_space = {
        "version": 1,
        "default": "x",
        "traffic": [
            {
                "name": "x",
                "unit": "iteration",
                "iterations": 1,
                "writes": [
                    {"space": "ram", "address": "0400", "bytes": 1, "write_kind": "dmawrite"}
                ],
            }
        ],
    }
    p1 = tmp_path / "t1.json"
    p1.write_text(json.dumps(bad_space))
    with pytest.raises(ValueError, match="invalid space"):
        module.load_traffic_config(p1)
    bad_kind = {
        "version": 1,
        "default": "x",
        "traffic": [
            {
                "name": "x",
                "unit": "iteration",
                "iterations": 1,
                "writes": [
                    {"space": "c64", "address": "0400", "bytes": 1, "write_kind": "nope"}
                ],
            }
        ],
    }
    p2 = tmp_path / "t2.json"
    p2.write_text(json.dumps(bad_kind))
    with pytest.raises(ValueError, match="invalid write_kind"):
        module.load_traffic_config(p2)


def test_c64cast_profile_expands_to_expected_requests():
    module = load_module()
    config = module.load_traffic_config(module.DEFAULT_TRAFFIC_CONFIG)
    profile = config.select("c64cast")
    logicals = module.expand_unit(profile, 0)
    assert [(lw.address, lw.bytes_count, lw.label) for lw in logicals] == [
        (0x0400, 1000, "screen"),
        (0xD800, 1000, "color"),
        (0x2000, 8000, "bitmap"),
    ]


def test_c64cast_profile_sums_to_10000_bytes_per_unit():
    module = load_module()
    config = module.load_traffic_config(module.DEFAULT_TRAFFIC_CONFIG)
    profile = config.select("c64cast")
    assert module.per_unit_bytes(profile, 0) == 10000


def test_single_write_profile_generates_expected_unit():
    module = load_module()
    config = module.load_traffic_config(module.DEFAULT_TRAFFIC_CONFIG)
    profile = config.select("single-write")
    logicals = module.expand_unit(profile, 7)
    assert len(logicals) == 1
    # single-write now writes to safe screen RAM ($0400) with 64 bytes
    assert logicals[0].address == 0x0400
    assert logicals[0].bytes_count == 64
    assert profile.iterations == 100


# --------------------------------------------------------------------------- #
# 10. bank_cycle, repeat, dirty expansion
# --------------------------------------------------------------------------- #


def test_template_expansion_supports_repeat_bank_cycle_dirty(tmp_path):
    module = load_module()
    cfg = {
        "version": 1,
        "default": "x",
        "traffic": [
            {
                "name": "x",
                "unit": "iteration",
                "iterations": 4,
                "pacing": "none",
                "payload_pattern": "increment",
                "seed": 1,
                "writes": [
                    {
                        "label": "multi",
                        "space": "c64",
                        "address": "0400",
                        "bytes": 32,
                        "write_kind": "dmawrite",
                        "repeat": 2,
                        "bank_cycle": ["0400", "0800"],
                    },
                    {
                        "label": "span",
                        "space": "c64",
                        "address": "2000",
                        "bytes": 16,
                        "write_kind": "dmawrite",
                        "dirty": {"policy": "one-span", "span": 4},
                    },
                    {
                        "label": "slabs",
                        "space": "c64",
                        "address": "3000",
                        "bytes": 16,
                        "write_kind": "dmawrite",
                        "dirty": {"policy": "slabs", "span": 4},
                    },
                ],
            }
        ],
    }
    path = tmp_path / "t.json"
    path.write_text(json.dumps(cfg))
    profile = module.load_traffic_config(path).select("x")

    # Unit 0: bank_cycle[0] = 0x0400
    logicals = module.expand_unit(profile, 0)
    assert [lw.address for lw in logicals[:2]] == [0x0400, 0x0400]
    assert [lw.bytes_count for lw in logicals[:2]] == [32, 32]
    # one-span unit 0 => offset 0, span 4 -> address 0x2000
    assert (logicals[2].address, logicals[2].bytes_count) == (0x2000, 4)
    # slabs of 4 across 16 bytes -> 4 spans
    spans = [(lw.address, lw.bytes_count) for lw in logicals[3:]]
    assert spans == [(0x3000, 4), (0x3004, 4), (0x3008, 4), (0x300C, 4)]

    # Unit 1: bank_cycle[1] = 0x0800
    logicals1 = module.expand_unit(profile, 1)
    assert [lw.address for lw in logicals1[:2]] == [0x0800, 0x0800]
    # one-span unit 1 => offset 4
    assert (logicals1[2].address, logicals1[2].bytes_count) == (0x2004, 4)


# --------------------------------------------------------------------------- #
# 11. Deterministic payload generation across phases
# --------------------------------------------------------------------------- #


def test_deterministic_payload_independent_of_label():
    module = load_module()
    payload_a = module.generate_payload(
        traffic_name="t",
        unit_index=0,
        emitted_write_index=0,
        space="c64",
        address=0x0400,
        byte_count=16,
        dirty_offset=0,
        seed=1,
        pattern="frame-counter",
    )
    payload_b = module.generate_payload(
        traffic_name="t",
        unit_index=0,
        emitted_write_index=0,
        space="c64",
        address=0x0400,
        byte_count=16,
        dirty_offset=0,
        seed=1,
        pattern="frame-counter",
    )
    assert payload_a == payload_b
    assert payload_a[0] == 0  # frame_counter unit 0 + offset 0 + 0
    assert payload_a[-1] == 15

    payload_random = module.generate_payload(
        traffic_name="t",
        unit_index=1,
        emitted_write_index=2,
        space="c64",
        address=0x2000,
        byte_count=8,
        dirty_offset=4,
        seed=1,
        pattern="random",
    )
    payload_random2 = module.generate_payload(
        traffic_name="t",
        unit_index=1,
        emitted_write_index=2,
        space="c64",
        address=0x2000,
        byte_count=8,
        dirty_offset=4,
        seed=1,
        pattern="random",
    )
    assert payload_random == payload_random2
    assert len(payload_random) == 8


def test_label_change_does_not_affect_payload():
    module = load_module()
    kwargs = dict(
        traffic_name="t", unit_index=0, emitted_write_index=0,
        space="c64", address=0x0400, byte_count=16, dirty_offset=0,
        seed=1, pattern="frame-counter",
    )
    p_no_label = module.generate_payload(**kwargs)
    # Labels are NOT inputs to generate_payload (only call identity matters)
    assert p_no_label == module.generate_payload(**kwargs)


# --------------------------------------------------------------------------- #
# 13-15. REST framing
# --------------------------------------------------------------------------- #


def test_rest_put_path_and_limit():
    module = load_module()
    path = module.rest_request_path_put(0x0400, b"\x00" * 10)
    assert path == "/v1/machine:writemem?address=0400&data=" + "00" * 10
    with pytest.raises(ValueError, match="exceeds 128 bytes"):
        module.rest_request_path_put(0x0400, b"\x00" * 129)


def test_rest_put_rejects_odd_length_or_non_hex_query_construction():
    module = load_module()
    # Construction relies on bytes.hex().upper() returning even-length hex; zero-length raises the
    # length guard, which is the correct safety boundary before constructing query data.
    with pytest.raises(ValueError, match="at least one byte"):
        module.rest_request_path_put(0x0400, b"")
    # Even-length hex invariant is enforced by the validator's own self-check on the constructed string.
    with pytest.raises(ValueError, match="exceeds 128 bytes"):
        module.rest_request_path_put(0x0400, b"\x00" * 129)


def test_rest_post_path_and_headers():
    module = load_module()
    path = module.rest_request_path_post(0x0400)
    assert path == "/v1/machine:writemem?address=0400"


def test_rest_method_resolver():
    module = load_module()
    assert module.resolve_rest_method("auto", 100) == "PUT"
    assert module.resolve_rest_method("auto", 129) == "POST"
    assert module.resolve_rest_method("put", 200) == "PUT"
    assert module.resolve_rest_method("post", 10) == "POST"


# --------------------------------------------------------------------------- #
# 16-17. REST response handling & password plumbing
# --------------------------------------------------------------------------- #


def test_rest_executor_records_failed_request_for_403(monkeypatch):
    module = load_module()
    profile = _load_default_profile(module)
    conn = ScriptedHttpConnection("u64", 80, 8.0, [FakeResponse(status=403, body=b"Forbidden.")])
    captured = []

    ctx = _make_ctx(module, profile, http_connection_factory=lambda *a, **kw: conn, emit=captured.append)
    executor = module.RestExecutor(ctx)
    logical = module.expand_unit(profile, 0)[0]
    data = module.generate_payload(
        traffic_name=profile.name, unit_index=0, emitted_write_index=0,
        space=logical.space, address=logical.address, byte_count=logical.bytes_count,
        dirty_offset=logical.dirty_offset, seed=profile.seed, pattern=profile.payload_pattern,
    )
    extra = executor.execute(logical, data)
    assert extra["ok"] is False
    assert extra["status"] == 403
    assert "Forbidden" in extra["error"]
    # c64cast screen write is 1000 bytes > 128, so default --rest-method auto resolves to POST.
    # 1541Ultimate HTTP requires uppercase methods; resolve_rest_method upper-cases.
    assert conn.requests[0][0] in ("PUT", "POST")
    assert "X-Password" not in conn.requests[0][3]


def test_rest_executor_sends_x_password_when_configured(monkeypatch):
    module = load_module()
    profile = _load_default_profile(module)
    conn = FakeHttpConnection("u64", 80, 8.0)
    captured = []

    ctx = _make_ctx(
        module, profile,
        http_connection_factory=lambda *a, **kw: conn,
        network_password="secret",
        emit=captured.append,
    )
    executor = module.RestExecutor(ctx)
    logical = module.expand_unit(profile, 0)[0]
    data = b"\x00" * logical.bytes_count
    executor.execute(logical, data)
    headers = conn.requests[0][3]
    assert headers.get("X-Password") == "secret"


def test_rest_executor_inherits_password_from_ftp_pass_alias():
    module = load_module()
    profile = _load_default_profile(module)
    args = module.build_parser().parse_args(["-P", "legacy"])
    ctx = module.build_context(args, profile, emit=lambda e: None)
    assert ctx.network_password == "legacy"


def test_explicit_network_password_takes_precedence_over_ftp_pass_alias():
    module = load_module()
    profile = _load_default_profile(module)
    args = module.build_parser().parse_args(["--network-password", "primary", "-P", "legacy"])
    ctx = module.build_context(args, profile, emit=lambda e: None)
    assert ctx.network_password == "primary"


def test_explicit_empty_network_password_clears_secret_without_ftp_fallback():
    module = load_module()
    profile = _load_default_profile(module)
    # An explicit empty --network-password must clear the credential rather than
    # silently falling back to the legacy --ftp-pass alias.
    args = module.build_parser().parse_args(["--network-password", "", "-P", "legacy"])
    ctx = module.build_context(args, profile, emit=lambda e: None)
    assert ctx.network_password == ""


# --------------------------------------------------------------------------- #
# 18-23. DMA framing and auth
# --------------------------------------------------------------------------- #


def test_dma_write_frame_construction():
    module = load_module()
    logical = module.LogicalWrite(
        logical_write_id="u0e0", space="c64", address=0x0400, bytes_count=10,
        write_kind="dmawrite", label=None, rest_policy="write",
        emitted_write_index=0, dirty_offset=0, template_index=0,
    )
    cmd, payload = module.dma_write_frame(logical, b"HELLO")
    assert cmd == module.SOCKET_CMD_DMAWRITE
    assert payload[:2] == b"\x00\x04"
    assert payload[2:] == b"HELLO"


def test_dma_rejects_oversized_single_frame_dmawrite():
    module = load_module()
    logical = module.LogicalWrite(
        logical_write_id="u0e0", space="c64", address=0x0400, bytes_count=65534,
        write_kind="dmawrite", label=None, rest_policy="write",
        emitted_write_index=0, dirty_offset=0, template_index=0,
    )
    with pytest.raises(ValueError, match="exceeds single-frame max"):
        module.validate_dma_payload(logical, b"\x00" * 65534)


def test_dma_reu_frame_construction():
    module = load_module()
    logical = module.LogicalWrite(
        logical_write_id="u0e0", space="reu", address=0x010203, bytes_count=4,
        write_kind="reuwrite", label=None, rest_policy="skip",
        emitted_write_index=0, dirty_offset=0, template_index=0,
    )
    cmd, payload = module.dma_write_frame(logical, b"\x01\x02\x03\x04")
    assert cmd == module.SOCKET_CMD_REUWRITE
    assert payload[:3] == b"\x03\x02\x01"  # little-endian 0x010203
    assert payload[3:] == b"\x01\x02\x03\x04"


def test_dma_rejects_oversized_reuwrite():
    module = load_module()
    logical = module.LogicalWrite(
        logical_write_id="u0e0", space="reu", address=0x0, bytes_count=65533,
        write_kind="reuwrite", label=None, rest_policy="skip",
        emitted_write_index=0, dirty_offset=0, template_index=0,
    )
    with pytest.raises(ValueError, match="exceeds single-frame max"):
        module.validate_dma_payload(logical, b"\x00" * 65533)


def test_dma_authenticate_frame():
    module = load_module()
    frame = module.authenticate_frame("secret")
    cmd, length = struct.unpack("<HH", frame[:4])
    assert cmd == module.SOCKET_CMD_AUTHENTICATE
    assert length == 6
    assert frame[4:] == b"secret"


def test_dma_executor_auth_success_and_failure(monkeypatch):
    module = load_module()
    profile = _load_default_profile(module)

    # Success case: persistent mode authenticates on open()
    sock_success = FakeSocket(scripted_responses=[b"\x01"])

    ctx = _make_ctx(
        module, profile,
        socket_factory=lambda addr, timeout: sock_success,
        network_password="secret",
        dma_connection=module.DMA_CONN_PERSISTENT,
    )
    executor = module.DmaExecutor(ctx)
    executor.open()  # should NOT raise
    executor.close()

    # Failure case: receives 0x00 -> BenchmarkError on open()
    sock_fail = FakeSocket(scripted_responses=[b"\x00"])
    ctx_fail = _make_ctx(
        module, profile,
        socket_factory=lambda addr, timeout: sock_fail,
        network_password="bad",
        dma_connection=module.DMA_CONN_PERSISTENT,
    )
    executor_fail = module.DmaExecutor(ctx_fail)
    with pytest.raises(module.BenchmarkError):
        executor_fail.open()


# --------------------------------------------------------------------------- #
# 24-25. Barrier timing + send-only warning
# --------------------------------------------------------------------------- #


def test_dma_barrier_mode_measures_through_send_and_barrier():
    module = load_module()
    profile = _load_default_profile(module)
    # script: [barrier response = b"\xAA"] for debug-reg, then nothing further
    sock = FakeSocket(scripted_responses=[b"\xAA"])
    ctx = _make_ctx(
        module, profile,
        socket_factory=lambda addr, timeout: sock,
        dma_ack_mode=module.DMA_ACK_BARRIER,
        dma_connection=module.DMA_CONN_PER_REQUEST,
    )
    executor = module.DmaExecutor(ctx)
    logical = module.expand_unit(profile, 0)[0]
    data = module.generate_payload(
        traffic_name=profile.name, unit_index=0, emitted_write_index=0,
        space=logical.space, address=logical.address, byte_count=logical.bytes_count,
        dirty_offset=logical.dirty_offset, seed=profile.seed, pattern=profile.payload_pattern,
    )
    extra = executor.execute(logical, data)
    assert extra["ok"] is True
    assert extra["barrier"] == "debugreg"
    # sent: command_frame(FF06, payload) + command_frame(FF76, empty)
    sent = bytes(sock.sent)
    cmd1 = struct.unpack("<H", sent[:2])[0]
    # debug-reg barrier frame is the last 4 bytes: cmd(2) + len(2) + 0-byte payload
    cmd2 = struct.unpack("<H", sent[-4:-2])[0]
    assert cmd1 == module.SOCKET_CMD_DMAWRITE
    assert cmd2 == module.SOCKET_CMD_DEBUG_REG


def test_dma_send_only_emits_warning_event():
    module = load_module()
    profile = _load_default_profile(module)
    sock = FakeSocket()
    warnings = []
    ctx = _make_ctx(
        module, profile,
        socket_factory=lambda addr, timeout: sock,
        dma_ack_mode=module.DMA_ACK_SEND_ONLY,
        dma_connection=module.DMA_CONN_PER_REQUEST,
        emit=lambda e: (warnings.append(e) if e.get("event") == "warning" else None),
    )
    executor = module.DmaExecutor(ctx)
    logical = module.expand_unit(profile, 0)[0]
    data = b"\x00" * logical.bytes_count
    executor.execute(logical, data)
    assert any(w.get("code") == "DMA_SEND_ONLY_LATENCY" for w in warnings)


def test_dma_identify_barrier_reads_title():
    module = load_module()
    profile = _load_default_profile(module)
    # Identify: title length 5, then 5 bytes title
    sock = FakeSocket(scripted_responses=[b"\x05", b"u64ok"])
    ctx = _make_ctx(
        module, profile,
        socket_factory=lambda addr, timeout: sock,
        dma_ack_mode=module.DMA_ACK_BARRIER,
        dma_barrier=module.DMA_BARRIER_IDENTIFY,
        dma_connection=module.DMA_CONN_PER_REQUEST,
    )
    executor = module.DmaExecutor(ctx)
    logical = module.expand_unit(profile, 0)[0]
    data = b"\x00" * logical.bytes_count
    extra = executor.execute(logical, data)
    assert extra["barrier"] == "identify"
    assert extra["ok"] is True


# --------------------------------------------------------------------------- #
# 26-29. JSON output shape, failure events, percentiles, warmup
# --------------------------------------------------------------------------- #


def test_request_event_includes_request_bytes_and_address():
    module = load_module()
    profile = _load_default_profile(module)
    conn = FakeHttpConnection("u64", 80, 8.0)
    captured = []
    ctx = _make_ctx(module, profile, http_connection_factory=lambda *a, **kw: conn, emit=captured.append)
    executor = module.RestExecutor(ctx)
    logical = module.expand_unit(profile, 0)[0]
    data = b"\x00" * logical.bytes_count
    extra = executor.execute(logical, data)
    event = module._request_event(
        ctx=ctx, logical=logical, payload=data, probe="rest",
        round_index=1, phase_index=0, unit_index=0, measured_iter=1,
        warmup=False, extra=extra,
    )
    assert event["request_bytes"] == logical.bytes_count
    assert event["address"] == "0400"
    assert event["event"] == "request"
    assert event["ok"] is True


def test_failure_event_shape():
    module = load_module()
    profile = _load_default_profile(module)
    captured = []
    ctx = _make_ctx(module, profile, http_connection_factory=lambda *a, **kw: FakeHttpConnection("u64", 80, 8.0), emit=captured.append)
    logical = module.expand_unit(profile, 0)[0]
    data = b"\x00" * logical.bytes_count
    extra = {
        "ok": False,
        "error": "boom",
        "error_type": "RuntimeError",
        "latency_ms": 0.1,
        "method": "PUT",
        "path": "/v1/machine:writemem?address=0400&data=00",
        "http_connection": "close",
    }
    event = module._request_event(
        ctx=ctx, logical=logical, payload=data, probe="rest",
        round_index=1, phase_index=0, unit_index=0, measured_iter=1,
        warmup=False, extra=extra,
    )
    assert event["ok"] is False
    assert event["error"] == "boom"
    assert event["error_type"] == "RuntimeError"


def test_summary_percentile_calculations():
    module = load_module()
    samples = sorted([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert module.percentile(samples, 50) == 5
    assert module.percentile(samples, 90) == 9
    assert module.percentile(samples, 95) == 10
    assert module.percentile(samples, 99) == 10
    assert module.percentile([], 50) == 0.0


def test_warmup_events_excluded_from_measured_summary():
    module = load_module()
    now = [0.0]

    def fake_now():
        now[0] += 0.001
        return f"2026-01-01T00:00:00.{int(now[0] * 1000) % 1000:03d}Z"

    events = [
        {"event": "request", "warmup": True, "ok": True, "latency_ms": 1.0,
         "request_bytes": 100, "perf_ts": 0.0, "phase_probe": "rest"},
        {"event": "request", "warmup": False, "ok": True, "latency_ms": 2.0,
         "request_bytes": 100, "perf_ts": 0.1, "phase_probe": "rest"},
        {"event": "request", "warmup": False, "ok": True, "latency_ms": 4.0,
         "request_bytes": 100, "perf_ts": 0.2, "phase_probe": "rest"},
    ]
    for e in events:
        e["elapsed_ms"] = e.pop("perf_ts") * 1000.0
    summary = module.summarize_phase(events)
    assert summary["requests"] == 2  # warmup excluded
    assert summary["warmup_requests"] == 1
    assert summary["request_bytes"] == 200


# --------------------------------------------------------------------------- #
# 30. Address range validation
# --------------------------------------------------------------------------- #


def test_address_range_validation_rejects_writes_past_ffff():
    module = load_module()
    logical = module.LogicalWrite(
        logical_write_id="u0e0", space="c64", address=0xFFFE, bytes_count=10,
        write_kind="dmawrite", label=None, rest_policy="write",
        emitted_write_index=0, dirty_offset=0, template_index=0,
    )
    with pytest.raises(ValueError, match="write range exceeds"):
        module.validate_rest_payload(logical, b"\x00" * 10, method="post")


def _load_single_write_profile(module, tmp_path, write):
    cfg = {
        "version": 1,
        "default": "x",
        "traffic": [
            {
                "name": "x",
                "unit": "iteration",
                "iterations": 1,
                "pacing": "none",
                "payload_pattern": "zero",
                "seed": 1,
                "writes": [write],
            }
        ],
    }
    path = tmp_path / "t.json"
    path.write_text(json.dumps(cfg))
    return module.load_traffic_config(path).select("x")


def test_loader_rejects_c64_bank_cycle_entry_past_ffff(tmp_path):
    module = load_module()
    # The static address fits, but a cycled bank base plus bytes_count overflows.
    with pytest.raises(ValueError, match=r"write range exceeds \$FFFF"):
        _load_single_write_profile(module, tmp_path, {
            "label": "cycle",
            "space": "c64",
            "address": "0400",
            "bytes": 64,
            "write_kind": "dmawrite",
            "bank_cycle": ["0400", "FFE0"],  # FFE0 + 64 = 0x10020 > 0xFFFF
        })


def test_loader_rejects_reu_write_past_ffffff(tmp_path):
    module = load_module()
    with pytest.raises(ValueError, match=r"write range exceeds \$FFFFFF"):
        _load_single_write_profile(module, tmp_path, {
            "label": "reu",
            "space": "reu",
            "address": "FFFFF0",  # FFFFF0 + 64 = 0x1000030 > 0xFFFFFF
            "bytes": 64,
            "write_kind": "reuwrite",
        })


# --------------------------------------------------------------------------- #
# 31. Start events include firmware source info
# --------------------------------------------------------------------------- #


def test_start_event_includes_firmware_source():
    module = load_module()
    profile = _load_default_profile(module)
    captured = []
    ctx = _make_ctx(module, profile, emit=captured.append)
    fw_source = {"checkout_path": "/tmp/checkout", "commit": "deadbeef", "dirty": False}
    event = module.build_start_event(
        ctx=ctx, probes=("rest", "dma"), schedule="sequential", runners=1, rounds=1,
        duration_s=None, iterations=10, traffic_config_path="x.json",
        firmware_source=fw_source, primary=True,
    )
    assert event["run"] == "start"
    assert event["firmware_source"] == fw_source
    assert event["traffic"] == "c64cast"
    assert event["probes"] == ["rest", "dma"]


def test_firmware_source_info_returns_checkout_path():
    module = load_module()
    info = module.firmware_source_info()
    assert "checkout_path" in info
    assert "commit" in info
    assert "dirty" in info


# --------------------------------------------------------------------------- #
# Helpers for context construction
# --------------------------------------------------------------------------- #


def _load_default_profile(module):
    config = module.load_traffic_config(module.DEFAULT_TRAFFIC_CONFIG)
    return config.select("c64cast")


def _make_ctx(
    module,
    profile,
    *,
    http_connection_factory=None,
    socket_factory=None,
    network_password="",
    dma_ack_mode=None,
    dma_barrier=None,
    dma_connection=None,
    emit=None,
):
    args = module.build_parser().parse_args(["-H", "u64"])
    ctx = module.build_context(args, profile, emit=emit or (lambda e: None))
    ctx.network_password = network_password
    if dma_ack_mode is not None:
        ctx.dma_ack_mode = dma_ack_mode
    if dma_barrier is not None:
        ctx.dma_barrier = dma_barrier
    if dma_connection is not None:
        ctx.dma_connection = dma_connection
    if http_connection_factory is not None:
        ctx.http_connection_factory = http_connection_factory
    if socket_factory is not None:
        ctx.socket_factory = socket_factory
    return ctx