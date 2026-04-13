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
    assert resolved.probes == ("ftp", "telnet", "http", "ftp", "telnet", "ping")
    assert resolved.schedule == "concurrent"
    assert resolved.runners == 5
    assert resolved.duration_s == 120
    assert resolved.probe_correctness == {
        "ping": module.ProbeCorrectness.CORRECT,
        "http": module.ProbeCorrectness.CORRECT,
        "ftp": module.ProbeCorrectness.INCOMPLETE,
        "telnet": module.ProbeCorrectness.INCOMPLETE,
    }
    assert resolved.probe_surfaces == {
        "ping": module.ProbeSurface.SMOKE,
        "http": module.ProbeSurface.READWRITE,
        "ftp": module.ProbeSurface.READWRITE,
        "telnet": module.ProbeSurface.READWRITE,
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


def test_global_surface_and_mode_apply_with_per_protocol_fallbacks():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--surface", "readwrite", "--mode", "invalid"]))

    assert resolved.probe_surfaces == {
        "ping": module.ProbeSurface.SMOKE,
        "http": module.ProbeSurface.READWRITE,
        "ftp": module.ProbeSurface.READWRITE,
        "telnet": module.ProbeSurface.READWRITE,
    }
    assert resolved.probe_correctness == {
        "ping": module.ProbeCorrectness.CORRECT,
        "http": module.ProbeCorrectness.CORRECT,
        "ftp": module.ProbeCorrectness.INVALID,
        "telnet": module.ProbeCorrectness.INCOMPLETE,
    }
    assert resolved.overrides == ("surface", "mode")


def test_protocol_specific_mode_falls_back_when_unsupported():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--http-mode", "incomplete", "--telnet-mode", "invalid"]))

    assert resolved.probe_correctness["http"] == module.ProbeCorrectness.CORRECT
    assert resolved.probe_correctness["telnet"] == module.ProbeCorrectness.INCOMPLETE
    assert resolved.overrides == ("http-mode", "telnet-mode")


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
    assert "--surface" in help_text
    assert "--mode" in help_text
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


def test_ftp_read_surface_rotates_across_multiple_operation_names(monkeypatch):
    module = load_module()

    class FakeFTP:
        def pwd(self):
            return "/"

        def nlst(self, path):
            if path == ".":
                return ["a", "b", "c"]
            if path == module.FTP_TEMP_DIR:
                return [f"{module.FTP_SELF_FILE_PREFIX}existing.txt"]
            raise AssertionError(path)

        def retrlines(self, command, callback):
            assert command == "LIST ."
            callback("line 1")
            callback("line 2")

    fake_ftp = FakeFTP()
    monkeypatch.setattr(module, "_ftp_connect", lambda settings: fake_ftp)
    monkeypatch.setattr(module, "_ftp_close", lambda ftp: None)

    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile=None,
        probes=("ftp",),
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
        probe_surfaces={"ftp": module.ProbeSurface.READ},
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)

    details = []
    for iteration in range(1, 5):
        context = module.ProbeRuntimeContext(config=config, state=state, protocol="ftp", runner_id=1, iteration=iteration)
        previous = module._set_probe_context(context)
        try:
            outcome = module.run_ftp_probe(settings, module.ProbeCorrectness.CORRECT)
        finally:
            module._restore_probe_context(previous)
        details.append(outcome.detail)

    assert [detail.split()[1] for detail in details] == [
        "op=ftp_pwd",
        "op=ftp_nlst_root",
        "op=ftp_list_root",
        "op=ftp_nlst_temp",
    ]
    assert details[-1].endswith("entries=1 path=/Temp")


def test_run_extended_primes_temp_dir_before_ftp_read_surface(monkeypatch):
    module = load_module()
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile="soak",
        probes=("ftp",),
        schedule="sequential",
        runners=1,
        duration_s=1,
        probe_correctness={
            "ping": module.ProbeCorrectness.CORRECT,
            "http": module.ProbeCorrectness.CORRECT,
            "ftp": module.ProbeCorrectness.CORRECT,
            "telnet": module.ProbeCorrectness.CORRECT,
        },
        uses_extended_flags=True,
        overrides=(),
        probe_surfaces={"ftp": module.ProbeSurface.READ},
    )
    calls = []

    monkeypatch.setattr(module, "_ftp_prime_temp_dir", lambda current_settings, minimum_count=1: calls.append((current_settings.host, minimum_count)))
    monkeypatch.setattr(module, "run_runner_loop", lambda *args, **kwargs: 0)

    assert module.run_extended(config, settings) == 0
    assert calls == [("host", 1)]


def test_ftp_prime_temp_dir_seeds_known_files_without_listing(monkeypatch):
    module = load_module()
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    fake_ftp = object()
    calls = []

    monkeypatch.setattr(module, "_ftp_connect", lambda current_settings: calls.append(("connect", current_settings.host)) or fake_ftp)
    monkeypatch.setattr(module, "_ftp_close", lambda ftp: calls.append(("close", ftp)))
    monkeypatch.setattr(
        module,
        "_ftp_seed_self_file",
        lambda current_settings, ftp, ordinal: calls.append(("seed", ftp, ordinal)) or f"/Temp/{ordinal}.txt",
    )

    seeded = module._ftp_prime_temp_dir(settings, minimum_count=2)

    assert seeded == ("/Temp/1.txt", "/Temp/2.txt")
    assert calls == [
        ("connect", "host"),
        ("seed", fake_ftp, 1),
        ("seed", fake_ftp, 2),
        ("close", fake_ftp),
    ]


def test_ftp_readwrite_surface_rotates_across_mutating_operation_names(monkeypatch):
    module = load_module()

    class FakeFTP:
        def __init__(self):
            self.temp_files = {}

        def pwd(self):
            return "/"

        def nlst(self, path):
            if path == ".":
                return ["a", "b", "c"]
            if path == module.FTP_TEMP_DIR:
                return sorted(entry.rsplit("/", 1)[-1] for entry in self.temp_files)
            raise AssertionError(path)

        def retrlines(self, command, callback):
            assert command == "LIST ."
            callback("line 1")

        def storbinary(self, command, payload):
            path = command.split(" ", 1)[1]
            self.temp_files[path] = payload.read()

        def retrbinary(self, command, callback):
            path = command.split(" ", 1)[1]
            callback(self.temp_files[path])

        def rename(self, source, target):
            self.temp_files[target] = self.temp_files.pop(source)

        def delete(self, path):
            self.temp_files.pop(path, None)

    fake_ftp = FakeFTP()
    monkeypatch.setattr(module, "_ftp_connect", lambda settings: fake_ftp)
    monkeypatch.setattr(module, "_ftp_close", lambda ftp: None)

    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile=None,
        probes=("ftp",),
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
        probe_surfaces={"ftp": module.ProbeSurface.READWRITE},
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)

    details = []
    for iteration in range(1, 9):
        context = module.ProbeRuntimeContext(config=config, state=state, protocol="ftp", runner_id=1, iteration=iteration)
        previous = module._set_probe_context(context)
        try:
            outcome = module.run_ftp_probe(settings, module.ProbeCorrectness.CORRECT)
        finally:
            module._restore_probe_context(previous)
        details.append(outcome.detail)

    assert [detail.split()[1] for detail in details] == [
        "op=ftp_pwd",
        "op=ftp_nlst_root",
        "op=ftp_list_root",
        "op=ftp_nlst_temp",
        "op=ftp_create_self_file",
        "op=ftp_read_self_file",
        "op=ftp_rename_self_file",
        "op=ftp_delete_self_file",
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


def test_telnet_open_menu_retries_with_vt_f2_sequence():
    module = load_module()
    calls = []

    class FakeSocket:
        def __init__(self):
            self.frames = [
                [socket.timeout(), socket.timeout(), socket.timeout()],
                [socket.timeout(), socket.timeout(), socket.timeout()],
                [b"Audio Mixer\nSpeaker Settings", socket.timeout(), socket.timeout(), socket.timeout()],
            ]
            self.frame_index = 0
            self.chunk_index = 0

        def sendall(self, payload):
            calls.append(("sendall", payload))

        def recv(self, size):
            del size
            if self.frame_index >= len(self.frames):
                raise socket.timeout()
            frame = self.frames[self.frame_index]
            item = frame[self.chunk_index]
            self.chunk_index += 1
            if self.chunk_index >= len(frame):
                self.frame_index += 1
                self.chunk_index = 0
            if isinstance(item, BaseException):
                raise item
            return item

    detail = module._telnet_open_menu(FakeSocket())

    assert detail == "visible_bytes=28"
    assert calls == [
        ("sendall", b"\x1b[12~"),
        ("sendall", b"\x1b[12~"),
    ]


def test_telnet_session_open_audio_mixer_uses_enter_after_returning_from_audio_mixer(monkeypatch):
    module = load_module()
    calls = []
    menu_text = "-- Audio / Video -- Video Configuration Audio Mixer Speaker Settings"
    audio_text = "Vol UltiSid 1 0 dB"
    session = module.TelnetRunnerSession(sock=object(), view_state="audio_mixer", last_text=audio_text)

    def fake_send(current_session, payload, *, view_state=None):
        calls.append(payload)
        if payload == module.TELNET_KEY_LEFT:
            current_session.last_text = menu_text
            if view_state is not None:
                current_session.view_state = view_state
            return menu_text
        if payload == module.TELNET_KEY_ENTER:
            current_session.last_text = audio_text
            if view_state is not None:
                current_session.view_state = view_state
            return audio_text
        raise AssertionError(payload)

    monkeypatch.setattr(module, "_telnet_session_send", fake_send)

    detail = module._telnet_session_open_menu(session)
    text = module._telnet_session_open_audio_mixer(session)

    assert detail == f"visible_bytes={len(menu_text.encode())}"
    assert text == audio_text
    assert calls == [module.TELNET_KEY_LEFT, module.TELNET_KEY_ENTER]
    assert session.view_state == "audio_mixer"
    assert session.menu_focus == "audio_mixer"


def test_telnet_session_open_audio_mixer_uses_down_after_f2_menu(monkeypatch):
    module = load_module()
    calls = []
    menu_text = "-- Audio / Video -- Video Configuration Audio Mixer Speaker Settings"
    down_text = "Video Configuration Audio Mixer"
    audio_text = "Vol UltiSid 1 0 dB"
    session = module.TelnetRunnerSession(sock=object())

    def fake_read(current_session, *, max_empty_reads=1, view_state=None):
        del current_session, max_empty_reads, view_state
        return ""

    def fake_send(current_session, payload, *, view_state=None):
        calls.append(payload)
        if payload == module.TELNET_KEY_F2:
            current_session.last_text = menu_text
            if view_state is not None:
                current_session.view_state = view_state
            return menu_text
        if payload == module.TELNET_KEY_DOWN:
            current_session.last_text = down_text
            if view_state is not None:
                current_session.view_state = view_state
            return down_text
        if payload == module.TELNET_KEY_ENTER:
            current_session.last_text = audio_text
            if view_state is not None:
                current_session.view_state = view_state
            return audio_text
        raise AssertionError(payload)

    monkeypatch.setattr(module, "_telnet_session_read", fake_read)
    monkeypatch.setattr(module, "_telnet_session_send", fake_send)

    text = module._telnet_session_open_audio_mixer(session)

    assert text == audio_text
    assert calls == [module.TELNET_KEY_F2, module.TELNET_KEY_DOWN, module.TELNET_KEY_ENTER]
    assert session.view_state == "audio_mixer"
    assert session.menu_focus == "audio_mixer"


def test_telnet_session_smoke_connect_reads_even_with_cached_text(monkeypatch):
    module = load_module()
    calls = []
    session = module.TelnetRunnerSession(
        sock=object(),
        view_state="audio_mixer",
        last_text="Vol UltiSid 1 0 dBVol UltiSid 2 0 dB",
    )

    def fake_read(current_session, *, max_empty_reads=1, view_state=None):
        calls.append((max_empty_reads, view_state))
        del current_session
        return ""

    monkeypatch.setattr(module, "_telnet_session_read", fake_read)

    detail = module._telnet_session_smoke_connect(session)

    assert detail == f"visible_bytes={len(session.last_text.encode())}"
    assert calls == [(1, "audio_mixer")]


def test_telnet_session_read_audio_mixer_item_refreshes_from_cached_audio_mixer(monkeypatch):
    module = load_module()
    calls = []
    menu_text = "-- Audio / Video -- Video Configuration Audio Mixer Speaker Settings"
    audio_text = "Vol UltiSid 1 0 dBVol UltiSid 2 0 dB"
    session = module.TelnetRunnerSession(
        sock=object(),
        view_state="audio_mixer",
        last_text=audio_text,
        menu_focus="audio_mixer",
    )

    def fake_send(current_session, payload, *, view_state=None):
        calls.append(payload)
        if payload == module.TELNET_KEY_LEFT:
            current_session.last_text = menu_text
            if view_state is not None:
                current_session.view_state = view_state
            return menu_text
        if payload == module.TELNET_KEY_ENTER:
            current_session.last_text = audio_text
            if view_state is not None:
                current_session.view_state = view_state
            return audio_text
        raise AssertionError(payload)

    monkeypatch.setattr(module, "_telnet_session_send", fake_send)

    detail = module._telnet_session_read_audio_mixer_item(session)

    assert detail == "current=0 dB"
    assert calls == [module.TELNET_KEY_LEFT, module.TELNET_KEY_ENTER]
    assert session.view_state == "audio_mixer"


def test_telnet_session_write_audio_mixer_item_recovers_from_partial_screen(monkeypatch):
    module = load_module()
    session = module.TelnetRunnerSession(sock=object(), view_state="audio_mixer", last_text="Vol UltiSid 1")
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)

    monkeypatch.setattr(module, "_telnet_session_refresh_audio_mixer", lambda current_session: "Vol UltiSid 1")
    monkeypatch.setattr(
        module,
        "_telnet_session_read",
        lambda current_session, *, max_empty_reads=1, view_state=None: " 0 dBVol UltiSid 2 0 dB",
    )
    monkeypatch.setattr(module, "_telnet_audio_mixer_write_right_steps", lambda current_settings, current, target: 0)

    detail = module._telnet_session_write_audio_mixer_item(settings, session, "0 dB")

    assert detail == "from=0 dB to=0 dB right_steps=0"
    assert session.last_text == "Vol UltiSid 1 0 dBVol UltiSid 2 0 dB"
    assert session.view_state == "audio_mixer"


def test_extended_ftp_incomplete_mode_uses_surface_specific_incomplete_operations(monkeypatch):
    module = load_module()
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile="stress",
        probes=("ftp",),
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
        probe_surfaces={"ftp": module.ProbeSurface.READWRITE},
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)
    monkeypatch.setattr(
        module,
        "_ftp_incomplete_operations",
        lambda surface: (("ftp_pasv_only_abort", lambda current_settings: f"surface={surface.value}"),),
    )

    previous = module._set_probe_context(module.ProbeRuntimeContext(config=config, state=state, protocol="ftp", runner_id=1, iteration=1))
    try:
        outcome = module.run_ftp_probe(settings, module.ProbeCorrectness.INCOMPLETE)
    finally:
        module._restore_probe_context(previous)

    assert outcome.result == "OK"
    assert outcome.detail == "surface=readwrite op=ftp_pasv_only_abort surface=readwrite"


def test_extended_telnet_incomplete_mode_uses_surface_specific_incomplete_operations(monkeypatch):
    module = load_module()
    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)
    config = module.ExecutionConfig(
        profile="stress",
        probes=("telnet",),
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
        probe_surfaces={"telnet": module.ProbeSurface.READWRITE},
    )
    state = module.ExecutionState(settings=settings, include_runner_context=False)
    monkeypatch.setattr(
        module,
        "_telnet_incomplete_operations",
        lambda surface: (("telnet_f2_abort", lambda current_settings: f"surface={surface.value}"),),
    )

    previous = module._set_probe_context(module.ProbeRuntimeContext(config=config, state=state, protocol="telnet", runner_id=1, iteration=1))
    try:
        outcome = module.run_telnet_probe(settings, module.ProbeCorrectness.INCOMPLETE)
    finally:
        module._restore_probe_context(previous)

    assert outcome.result == "OK"
    assert outcome.detail == "surface=readwrite op=telnet_f2_abort surface=readwrite"


def test_collect_telnet_visible_ignores_subnegotiation_and_keeps_literal_iac():
    module = load_module()
    replies = []

    class FakeHandle:
        def sendall(self, payload):
            replies.append(payload)

    chunk = bytes(
        [
            module.IAC,
            module.DO,
            1,
            ord("A"),
            module.IAC,
            module.SB,
            24,
            1,
            ord("x"),
            module.IAC,
            module.SE,
            module.IAC,
            module.IAC,
            ord("B"),
        ]
    )

    visible = module._collect_telnet_visible(FakeHandle(), chunk)

    assert visible == b"A\xffB"
    assert replies == [bytes([module.IAC, module.WONT, 1])]


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