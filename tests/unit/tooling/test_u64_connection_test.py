from __future__ import annotations

import importlib.util
import socket
import sys
import threading
import types
import uuid
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "u64_connection_test.py"


def load_module() -> types.ModuleType:
    module_name = f"test_u64_connection_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_main_without_new_flags_dispatches_to_legacy(monkeypatch):
    module = load_module()
    captured = {}

    def fake_run_legacy(settings):
        captured["settings"] = settings
        return 17

    monkeypatch.setattr(module, "run_legacy", fake_run_legacy)
    monkeypatch.setattr(module, "run_extended", lambda config, settings: pytest.fail("unexpected extended path"))

    assert module.main([]) == 17
    assert captured["settings"].host == module.HOST
    assert captured["settings"].delay_ms == module.INTER_CALL_DELAY_MS


def test_profile_main_defaults_host_to_u64_and_runs_extended(monkeypatch):
    module = load_module()
    captured = {}

    monkeypatch.setattr(module, "run_legacy", lambda settings: pytest.fail("unexpected legacy path"))

    def fake_run_extended(config, settings):
        captured["config"] = config
        captured["settings"] = settings
        return 23

    monkeypatch.setattr(module, "run_extended", fake_run_extended)

    assert module.main(["--profile", "soak"]) == 23
    assert captured["settings"].host == "u64"
    assert captured["config"].duration_s == 120


def test_soak_profile_resolves_to_legacy_safe_shape():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--profile", "soak"]))

    assert resolved.profile == "soak"
    assert resolved.probes == ("ping", "http", "ftp", "telnet")
    assert resolved.schedule == "sequential"
    assert resolved.runners == 1
    assert resolved.duration_s == 120
    assert resolved.probe_correctness == {
        "ping": module.ProbeCorrectness.CORRECT,
        "http": module.ProbeCorrectness.CORRECT,
        "ftp": module.ProbeCorrectness.CORRECT,
        "telnet": module.ProbeCorrectness.CORRECT,
    }


def test_stress_profile_resolves_deterministically():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--profile", "stress"]))

    assert resolved.profile == "stress"
    assert resolved.probes == ("ping", "http", "ftp", "telnet")
    assert resolved.schedule == "concurrent"
    assert resolved.runners == 4
    assert resolved.duration_s == 120
    assert resolved.probe_correctness == {
        "ping": module.ProbeCorrectness.CORRECT,
        "http": module.ProbeCorrectness.CORRECT,
        "ftp": module.ProbeCorrectness.INCOMPLETE,
        "telnet": module.ProbeCorrectness.INCOMPLETE,
    }


def test_explicit_low_level_flags_override_profile_values():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(
        parser.parse_args(
            [
                "--profile",
                "stress",
                "--probes",
                "ping,http",
                "--schedule",
                "sequential",
                "--runners",
                "2",
                "--ftp-mode",
                "correct",
            ]
        )
    )

    assert resolved.probes == ("ping", "http")
    assert resolved.schedule == "sequential"
    assert resolved.runners == 2
    assert resolved.duration_s == 120
    assert resolved.probe_correctness["ftp"] == module.ProbeCorrectness.CORRECT
    assert resolved.overrides == ("probes", "schedule", "runners", "ftp-mode")


def test_duration_override_replaces_profile_default():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--profile", "stress", "--duration-s", "300"]))

    assert resolved.duration_s == 300
    assert resolved.overrides == ("duration-s",)


def test_invalid_profile_fails_clearly():
    module = load_module()
    parser = module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--profile", "boom"])


def test_probe_parser_accepts_single_and_multiple_probes_in_order():
    module = load_module()

    assert module.parse_probes("ping") == ("ping",)
    assert module.parse_probes("ping,http,ftp") == ("ping", "http", "ftp")


@pytest.mark.parametrize("value", ["", "ping,,http", "ping,ssh", "ping,ping"])
def test_probe_parser_rejects_malformed_values(value):
    module = load_module()

    with pytest.raises(Exception):
        module.parse_probes(value)


def test_help_output_mentions_new_flags_and_precedence():
    module = load_module()
    help_text = module.build_parser().format_help()

    assert "--profile" in help_text
    assert "--probes" in help_text
    assert "--schedule" in help_text
    assert "--runners" in help_text
    assert "--duration-s" in help_text
    assert "--ping-mode" in help_text
    assert "--http-mode" in help_text
    assert "--ftp-mode" in help_text
    assert "--telnet-mode" in help_text
    assert "override the profile" in help_text
    assert "correct" in help_text
    assert "incomplete" in help_text
    assert "invalid" in help_text


def test_run_runner_iteration_sequential_keeps_probe_order():
    module = load_module()
    calls: list[tuple[str, object]] = []

    probe_runners = {
        "ping": lambda settings, mode: calls.append(("ping", mode)) or module.ProbeOutcome("OK", "ping reply", 1.0),
        "http": lambda settings, mode: calls.append(("http", mode)) or module.ProbeOutcome("OK", "HTTP 200 body_bytes=1", 1.0),
        "ftp": lambda settings, mode: calls.append(("ftp", mode)) or module.ProbeOutcome("OK", "NLST bytes=1", 1.0),
        "telnet": lambda settings, mode: calls.append(("telnet", mode)) or module.ProbeOutcome("OK", "banner_bytes=1", 1.0),
    }
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile=None,
        probes=("ping", "http", "ftp", "telnet"),
        schedule="sequential",
        runners=1,
        duration_s=None,
        probe_correctness={protocol: module.ProbeCorrectness.CORRECT for protocol in ("ping", "http", "ftp", "telnet")},
        uses_extended_flags=True,
        overrides=(),
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)

    module.run_runner_iteration(1, 1, config, settings, state, sleep_fn=lambda value: None, probe_runners=probe_runners)

    assert calls == [
        ("ping", module.ProbeCorrectness.CORRECT),
        ("http", module.ProbeCorrectness.CORRECT),
        ("ftp", module.ProbeCorrectness.CORRECT),
        ("telnet", module.ProbeCorrectness.CORRECT),
    ]


def test_run_runner_iteration_concurrent_allows_overlap():
    module = load_module()
    barrier = threading.Barrier(2)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def make_runner(name):
        def runner(settings, mode):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            barrier.wait(timeout=1)
            with lock:
                active -= 1
            return module.ProbeOutcome("OK", name, 1.0)

        return runner

    probe_runners = {
        "ping": make_runner("ping"),
        "http": make_runner("http"),
        "ftp": lambda settings, mode: module.ProbeOutcome("OK", "ftp", 1.0),
        "telnet": lambda settings, mode: module.ProbeOutcome("OK", "telnet", 1.0),
    }
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile=None,
        probes=("ping", "http"),
        schedule="concurrent",
        runners=1,
        duration_s=None,
        probe_correctness={
            "ping": module.ProbeCorrectness.CORRECT,
            "http": module.ProbeCorrectness.CORRECT,
            "ftp": module.ProbeCorrectness.CORRECT,
            "telnet": module.ProbeCorrectness.CORRECT,
        },
        uses_extended_flags=True,
        overrides=(),
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)

    module.run_runner_iteration(1, 1, config, settings, state, sleep_fn=lambda value: None, probe_runners=probe_runners)

    assert max_active == 2


def test_run_runner_iteration_sequential_converts_unexpected_probe_exceptions_to_failures():
    module = load_module()
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile=None,
        probes=("ping", "http"),
        schedule="sequential",
        runners=1,
        duration_s=None,
        probe_correctness={
            "ping": module.ProbeCorrectness.CORRECT,
            "http": module.ProbeCorrectness.CORRECT,
            "ftp": module.ProbeCorrectness.CORRECT,
            "telnet": module.ProbeCorrectness.CORRECT,
        },
        uses_extended_flags=True,
        overrides=(),
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)

    results = module.run_runner_iteration(
        1,
        1,
        config,
        settings,
        state,
        sleep_fn=lambda value: None,
        probe_runners={
            "ping": lambda settings, mode: (_ for _ in ()).throw(RuntimeError("boom")),
            "http": lambda settings, mode: module.ProbeOutcome("OK", "HTTP 200 body_bytes=1", 2.0),
            "ftp": lambda settings, mode: module.ProbeOutcome("OK", "NLST bytes=1", 3.0),
            "telnet": lambda settings, mode: module.ProbeOutcome("OK", "banner_bytes=1", 4.0),
        },
    )

    assert results[0][0] == "ping"
    assert results[0][1].result == "FAIL"
    assert results[0][1].detail == "ping failed: boom"
    assert len(state.latency_samples["ping"]) == 1
    assert len(state.latency_samples["http"]) == 1


def test_run_runner_iteration_concurrent_converts_unexpected_probe_exceptions_to_failures_without_dropping_results():
    module = load_module()
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile=None,
        probes=("ping", "http"),
        schedule="concurrent",
        runners=1,
        duration_s=None,
        probe_correctness={
            "ping": module.ProbeCorrectness.CORRECT,
            "http": module.ProbeCorrectness.CORRECT,
            "ftp": module.ProbeCorrectness.CORRECT,
            "telnet": module.ProbeCorrectness.CORRECT,
        },
        uses_extended_flags=True,
        overrides=(),
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)

    results = module.run_runner_iteration(
        1,
        1,
        config,
        settings,
        state,
        sleep_fn=lambda value: None,
        probe_runners={
            "ping": lambda settings, mode: (_ for _ in ()).throw(RuntimeError("boom")),
            "http": lambda settings, mode: module.ProbeOutcome("OK", "HTTP 200 body_bytes=1", 2.0),
            "ftp": lambda settings, mode: module.ProbeOutcome("OK", "NLST bytes=1", 3.0),
            "telnet": lambda settings, mode: module.ProbeOutcome("OK", "banner_bytes=1", 4.0),
        },
    )

    assert [protocol for protocol, _ in results] == ["ping", "http"]
    assert results[0][1].result == "FAIL"
    assert results[0][1].detail == "ping failed: boom"
    assert len(state.latency_samples["ping"]) == 1
    assert len(state.latency_samples["http"]) == 1


def test_multiple_runners_with_concurrent_probes_preserve_all_latency_samples():
    module = load_module()
    lock = threading.Lock()
    per_protocol_counts = {"ping": 0, "http": 0}
    base_latency_ms = {"ping": 1000.0, "http": 2000.0}

    def make_runner(protocol):
        def runner(settings, mode):
            del settings, mode
            with lock:
                per_protocol_counts[protocol] += 1
                call_index = per_protocol_counts[protocol]
            return module.ProbeOutcome("OK", f"{protocol} call={call_index}", base_latency_ms[protocol] + call_index)

        return runner

    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 100, False)
    config = module.ExecutionConfig(
        profile=None,
        probes=("ping", "http"),
        schedule="concurrent",
        runners=3,
        duration_s=None,
        probe_correctness={
            "ping": module.ProbeCorrectness.CORRECT,
            "http": module.ProbeCorrectness.CORRECT,
            "ftp": module.ProbeCorrectness.CORRECT,
            "telnet": module.ProbeCorrectness.CORRECT,
        },
        uses_extended_flags=True,
        overrides=(),
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)
    stop_event = threading.Event()
    probe_runners = {
        "ping": make_runner("ping"),
        "http": make_runner("http"),
        "ftp": lambda settings, mode: module.ProbeOutcome("OK", "ftp", 3.0),
        "telnet": lambda settings, mode: module.ProbeOutcome("OK", "telnet", 4.0),
    }
    threads = [
        threading.Thread(
            target=module.run_runner_loop,
            args=(runner_id, config, settings, state, stop_event),
            kwargs={
                "sleep_fn": lambda value: None,
                "probe_runners": probe_runners,
                "max_iterations": 2,
            },
        )
        for runner_id in (1, 2, 3)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    expected_count = 3 * 2
    assert per_protocol_counts == {"ping": expected_count, "http": expected_count}
    assert sorted(state.latency_samples["ping"]) == [base_latency_ms["ping"] + index for index in range(1, expected_count + 1)]
    assert sorted(state.latency_samples["http"]) == [base_latency_ms["http"] + index for index in range(1, expected_count + 1)]


def test_multiple_runners_scale_concurrency_per_probe_type():
    module = load_module()
    barrier = threading.Barrier(2)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def ping_runner(settings, mode):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        barrier.wait(timeout=1)
        with lock:
            active -= 1
        return module.ProbeOutcome("OK", "ping", 1.0)

    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile=None,
        probes=("ping",),
        schedule="sequential",
        runners=2,
        duration_s=None,
        probe_correctness={
            "ping": module.ProbeCorrectness.CORRECT,
            "http": module.ProbeCorrectness.CORRECT,
            "ftp": module.ProbeCorrectness.CORRECT,
            "telnet": module.ProbeCorrectness.CORRECT,
        },
        uses_extended_flags=True,
        overrides=(),
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)
    stop_event = threading.Event()
    threads = [
        threading.Thread(
            target=module.run_runner_loop,
            args=(runner_id, config, settings, state, stop_event),
            kwargs={
                "sleep_fn": lambda value: None,
                "probe_runners": {
                    "ping": ping_runner,
                    "http": lambda settings, mode: module.ProbeOutcome("OK", "http", 1.0),
                    "ftp": lambda settings, mode: module.ProbeOutcome("OK", "ftp", 1.0),
                    "telnet": lambda settings, mode: module.ProbeOutcome("OK", "telnet", 1.0),
                },
                "max_iterations": 1,
            },
        )
        for runner_id in (1, 2)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert max_active == 2


def test_normal_mode_defaults_are_safe_for_every_protocol():
    module = load_module()
    parser = module.build_parser()
    resolved = module.resolve_execution_config(parser.parse_args(["--profile", "soak"]))

    assert all(value is module.ProbeCorrectness.CORRECT for value in resolved.probe_correctness.values())


def test_invalid_probe_mode_fails_clearly():
    module = load_module()
    parser = module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--telnet-mode", "bad-mode"])


def test_dispatch_passes_resolved_modes_to_probe_runners():
    module = load_module()
    received: list[tuple[str, object]] = []

    def make_runner(name):
        def runner(settings, mode):
            received.append((name, mode))
            return module.ProbeOutcome("OK", name, 1.0)

        return runner

    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile="stress",
        probes=("ftp", "telnet"),
        schedule="sequential",
        runners=1,
        duration_s=120,
        probe_correctness={
            "ping": module.ProbeCorrectness.CORRECT,
            "http": module.ProbeCorrectness.CORRECT,
            "ftp": module.ProbeCorrectness.INCOMPLETE,
            "telnet": module.ProbeCorrectness.INCOMPLETE,
        },
        uses_extended_flags=True,
        overrides=(),
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)

    module.run_runner_iteration(
        1,
        1,
        config,
        settings,
        state,
        sleep_fn=lambda value: None,
        probe_runners={
            "ping": make_runner("ping"),
            "http": make_runner("http"),
            "ftp": make_runner("ftp"),
            "telnet": make_runner("telnet"),
        },
    )

    assert received == [
        ("ftp", module.ProbeCorrectness.INCOMPLETE),
        ("telnet", module.ProbeCorrectness.INCOMPLETE),
    ]


def test_http_normal_mode_requests_connection_close_and_reads_body(monkeypatch):
    module = load_module()
    calls = []

    class FakeResponse:
        status = 200

        def read(self):
            calls.append("read")
            return b"ok"

    class FakeConnection:
        def __init__(self, host, port, timeout):
            calls.append(("init", host, port, timeout))

        def request(self, method, path, headers):
            calls.append(("request", method, path, headers))

        def getresponse(self):
            calls.append("getresponse")
            return FakeResponse()

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.http.client, "HTTPConnection", FakeConnection)
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)

    outcome = module.run_http_probe(settings, module.ProbeCorrectness.CORRECT)

    assert outcome.result == "OK"
    assert ("request", "GET", "/v1/version", {"Connection": "close"}) in calls
    assert "read" in calls
    assert calls[-1] == "close"


def test_ftp_normal_mode_performs_login_pasv_nlst_quit_and_close():
    module = load_module()
    calls = []

    class FakeFTP:
        def connect(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
            return "220 ready"

        def login(self, user, password):
            calls.append(("login", user, password))
            return "230 logged in"

        def set_pasv(self, enabled):
            calls.append(("set_pasv", enabled))

        def nlst(self, path):
            calls.append(("nlst", path))
            return ["file1", "file2"]

        def quit(self):
            calls.append("quit")
            return "221 bye"

        def close(self):
            calls.append("close")

    module.ftplib.FTP = FakeFTP
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)

    outcome = module.run_ftp_probe(settings, module.ProbeCorrectness.CORRECT)

    assert outcome.result == "OK"
    assert calls == [
        ("connect", "host", 21, 8),
        ("login", "anonymous", ""),
        ("set_pasv", True),
        ("nlst", "."),
        "quit",
        "close",
    ]


def test_telnet_normal_mode_sends_newline_drains_banner_and_closes():
    module = load_module()
    calls = []

    class FakeSocket:
        def settimeout(self, timeout):
            calls.append(("settimeout", timeout))

        def sendall(self, payload):
            calls.append(("sendall", payload))

        def recv(self, size):
            calls.append(("recv", size))
            if calls.count(("recv", size)) == 1:
                return b"READY>"
            raise socket.timeout()

        def close(self):
            calls.append("close")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: FakeSocket())
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)

    try:
        outcome = module.run_telnet_probe(settings, module.ProbeCorrectness.CORRECT)
    finally:
        monkeypatch.undo()

    assert outcome.result == "OK"
    assert ("sendall", b"\r\n") in calls
    assert calls[-1] == "close"


def test_telnet_incomplete_correctness_does_not_send_probe_bytes_and_accepts_blank_session(monkeypatch):
    module = load_module()
    calls = []

    class FakeSocket:
        def settimeout(self, timeout):
            calls.append(("settimeout", timeout))

        def recv(self, size):
            calls.append(("recv", size))
            raise socket.timeout()

        def sendall(self, payload):
            calls.append(("sendall", payload))

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: FakeSocket())
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)

    outcome = module.run_telnet_probe(settings, module.ProbeCorrectness.INCOMPLETE)

    assert outcome.result == "OK"
    assert outcome.detail == "connected"
    assert not any(call == ("sendall", b"\r\n") for call in calls)
    assert calls[-1] == "close"


def test_historical_correctness_mapping_is_pinned_to_git_evidence():
    module = load_module()

    assert module.HISTORICAL_CORRECTNESS_EVIDENCE["ftp"]["incomplete"]["commit"] == "37314b1"
    assert module.HISTORICAL_CORRECTNESS_EVIDENCE["telnet"]["incomplete"]["commit"] == "37314b1"
    assert module.PROBE_CORRECTNESS_CHOICES["ping"] == (module.ProbeCorrectness.CORRECT,)
    assert module.PROBE_CORRECTNESS_CHOICES["http"] == (module.ProbeCorrectness.CORRECT,)


def test_ftp_incomplete_correctness_skips_quit_and_passive():
    module = load_module()
    calls = []

    class FakeFTP:
        def connect(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
            return "220 ready"

        def login(self, user, password):
            calls.append(("login", user, password))
            return "230 logged in"

        def set_pasv(self, enabled):
            calls.append(("set_pasv", enabled))

        def nlst(self, path):
            calls.append(("nlst", path))
            return ["file1"]

        def close(self):
            calls.append("close")

    module.ftplib.FTP = FakeFTP
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)

    outcome = module.run_ftp_probe(settings, module.ProbeCorrectness.INCOMPLETE)

    assert outcome.result == "OK"
    assert outcome.detail == "NLST bytes=5"
    assert calls == [
        ("connect", "host", 21, 8),
        ("login", "anonymous", ""),
        ("set_pasv", False),
        ("nlst", "."),
        "close",
    ]


def test_ftp_invalid_correctness_sends_wrong_command_without_quit():
    module = load_module()
    calls = []

    class FakeFTP:
        def connect(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
            return "220 ready"

        def login(self, user, password):
            calls.append(("login", user, password))
            return "230 logged in"

        def sendcmd(self, command):
            calls.append(("sendcmd", command))
            return "500 syntax error"

        def close(self):
            calls.append("close")

    module.ftplib.FTP = FakeFTP
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)

    outcome = module.run_ftp_probe(settings, module.ProbeCorrectness.INVALID)

    assert outcome.result == "OK"
    assert outcome.detail == "invalid_reply=500 syntax error"
    assert calls == [
        ("connect", "host", 21, 8),
        ("login", "anonymous", ""),
        ("sendcmd", "VIVIPI-WRONG"),
        "close",
    ]


def test_ping_probe_uses_ping_terminology():
    module = load_module()

    class Completed:
        returncode = 0
        stdout = "64 bytes from host: time=1.23 ms"
        stderr = ""

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Completed())
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)

    try:
        outcome = module.run_ping_probe(settings, "normal")
    finally:
        monkeypatch.undo()

    assert outcome.result == "OK"
    assert outcome.detail.startswith("ping_reply_ms=")