from __future__ import annotations

import threading
import time
import http.client

import pytest

from tests.unit.tooling._script_loader import load_script_module


def load_module():
    return load_script_module("u64_connection_test")


def load_runtime_module():
    return load_script_module("u64_connection_runtime")


def make_settings(module):
    return module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)


def make_config(module, *, probes=("ping", "http"), schedule="sequential", runners=1):
    return module.ExecutionConfig(
        profile=None,
        probes=probes,
        schedule=schedule,
        runners=runners,
        duration_s=None,
        probe_correctness={protocol: module.ProbeCorrectness.COMPLETE for protocol in module.PROBE_CHOICES},
        uses_extended_flags=True,
        overrides=(),
        probe_surfaces={
            "ping": module.ProbeSurface.SMOKE,
            "http": module.ProbeSurface.READWRITE,
            "ftp": module.ProbeSurface.READWRITE,
            "telnet": module.ProbeSurface.READWRITE,
            "ident": module.ProbeSurface.SMOKE,
            "raw64": module.ProbeSurface.READ,
            "modem": module.ProbeSurface.SMOKE,
        },
    )


def test_main_without_args_runs_default_soak_configuration(monkeypatch):
    module = load_module()
    captured = {}

    def fake_run_extended(config, settings):
        captured["config"] = config
        captured["settings"] = settings
        return 17

    monkeypatch.setattr(module, "run_extended", fake_run_extended)

    assert module.main([]) == 17
    assert captured["settings"].host == "u64"
    assert captured["config"].profile == "soak"
    assert captured["config"].probes == ("ping", "http", "ftp", "telnet", "ident", "raw64")
    assert captured["config"].schedule == "concurrent"
    assert captured["config"].runners == 1
    assert captured["config"].duration_s == 12 * 60 * 60
    assert captured["config"].probe_surfaces == {
        "ping": module.ProbeSurface.SMOKE,
        "http": module.ProbeSurface.READWRITE,
        "ftp": module.ProbeSurface.READWRITE,
        "telnet": module.ProbeSurface.READWRITE,
        "ident": module.ProbeSurface.SMOKE,
        "raw64": module.ProbeSurface.READ,
        "modem": module.ProbeSurface.SMOKE,
    }
    assert captured["config"].streams == ("audio", "video")


def test_stress_profile_resolves_deterministically():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--profile", "stress"]))

    assert resolved.profile == "stress"
    assert resolved.probes == ("raw64", "ftp", "telnet", "http", "ftp", "telnet", "ping", "ident")
    assert resolved.schedule == "concurrent"
    assert resolved.runners == 5
    assert resolved.duration_s == 120
    assert resolved.probe_correctness == {
        "ping": module.ProbeCorrectness.COMPLETE,
        "http": module.ProbeCorrectness.COMPLETE,
        "ftp": module.ProbeCorrectness.INCOMPLETE,
        "telnet": module.ProbeCorrectness.INCOMPLETE,
        "ident": module.ProbeCorrectness.COMPLETE,
        "raw64": module.ProbeCorrectness.COMPLETE,
        "modem": module.ProbeCorrectness.COMPLETE,
    }
    assert resolved.probe_surfaces == {
        "ping": module.ProbeSurface.SMOKE,
        "http": module.ProbeSurface.READWRITE,
        "ftp": module.ProbeSurface.READWRITE,
        "telnet": module.ProbeSurface.READWRITE,
        "ident": module.ProbeSurface.SMOKE,
        "raw64": module.ProbeSurface.READ,
        "modem": module.ProbeSurface.SMOKE,
    }
    assert resolved.streams == ()


def test_global_surface_and_mode_apply_with_fallbacks():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--surface", "readwrite", "--mode", "invalid"]))

    assert resolved.probe_surfaces == {
        "ping": module.ProbeSurface.SMOKE,
        "http": module.ProbeSurface.READWRITE,
        "ftp": module.ProbeSurface.READWRITE,
        "telnet": module.ProbeSurface.READWRITE,
        "ident": module.ProbeSurface.SMOKE,
        "raw64": module.ProbeSurface.READWRITE,
        "modem": module.ProbeSurface.SMOKE,
    }
    assert resolved.probe_correctness == {
        "ping": module.ProbeCorrectness.COMPLETE,
        "http": module.ProbeCorrectness.COMPLETE,
        "ftp": module.ProbeCorrectness.INVALID,
        "telnet": module.ProbeCorrectness.INCOMPLETE,
        "ident": module.ProbeCorrectness.COMPLETE,
        "raw64": module.ProbeCorrectness.COMPLETE,
        "modem": module.ProbeCorrectness.COMPLETE,
    }


def test_global_open_mode_applies_with_fallbacks():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--mode", "open"]))

    assert resolved.probe_correctness == {
        "ping": module.ProbeCorrectness.COMPLETE,
        "http": module.ProbeCorrectness.COMPLETE,
        "ftp": module.ProbeCorrectness.OPEN,
        "telnet": module.ProbeCorrectness.OPEN,
        "ident": module.ProbeCorrectness.COMPLETE,
        "raw64": module.ProbeCorrectness.COMPLETE,
        "modem": module.ProbeCorrectness.COMPLETE,
    }


def test_stream_flag_without_values_enables_all_streams():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--stream"]))

    assert resolved.streams == ("audio", "video")
    assert resolved.overrides == ("stream",)


def test_explicit_high_value_overrides_can_reenable_write_surface_with_single_stream():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(
        parser.parse_args(
            [
                "--duration-s",
                "30",
                "--surface",
                "readwrite",
                "--schedule",
                "sequential",
                "--stream",
                "video",
            ]
        )
    )

    assert resolved.profile == "soak"
    assert resolved.duration_s == 30
    assert resolved.schedule == "sequential"
    assert resolved.probe_surfaces == {
        "ping": module.ProbeSurface.SMOKE,
        "http": module.ProbeSurface.READWRITE,
        "ftp": module.ProbeSurface.READWRITE,
        "telnet": module.ProbeSurface.READWRITE,
        "ident": module.ProbeSurface.SMOKE,
        "raw64": module.ProbeSurface.READWRITE,
        "modem": module.ProbeSurface.SMOKE,
    }
    assert resolved.streams == ("video",)
    assert resolved.overrides == ("schedule", "duration-s", "surface", "stream")


def test_build_runtime_settings_uses_network_password_as_ftp_fallback_and_tracks_modem_port():
    module = load_module()
    parser = module.build_parser()

    settings = module.build_runtime_settings(parser.parse_args(["--network-password", "secret", "--modem-port", "3456"]))

    assert settings.network_password == "secret"
    assert settings.ftp_pass == "secret"
    assert settings.modem_port == 3456


def test_build_runtime_settings_uses_ftp_pass_as_legacy_shared_password_alias():
    module = load_module()
    parser = module.build_parser()

    settings = module.build_runtime_settings(parser.parse_args(["--ftp-pass", "legacy-secret"]))

    assert settings.network_password == "legacy-secret"
    assert settings.ftp_pass == "legacy-secret"


def test_parse_probes_accepts_ident_raw64_and_modem():
    module = load_module()

    assert module.parse_probes("ping,ident,raw64,modem") == ("ping", "ident", "raw64", "modem")


def test_explicit_probe_subset_disables_profile_streams_by_default():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--probes", "ident,raw64"]))

    assert resolved.probes == ("ident", "raw64")
    assert resolved.streams == ()
    assert resolved.overrides == ("probes",)


def test_validate_execution_config_rejects_concurrent_http_readwrite_runner_overflow():
    module = load_module()

    config = module.ExecutionConfig(
        profile="custom",
        probes=("http",),
        schedule=module.SCHEDULE_CONCURRENT,
        runners=module.u64_http.SCREEN_RAM_RUNNER_SLOT_COUNT + 1,
        duration_s=60,
        probe_correctness={protocol: module.ProbeCorrectness.COMPLETE for protocol in module.DEFAULT_PROBES},
        uses_extended_flags=True,
        overrides=(),
        probe_surfaces={
            "ping": module.ProbeSurface.SMOKE,
            "http": module.ProbeSurface.READWRITE,
            "ftp": module.ProbeSurface.READWRITE,
            "telnet": module.ProbeSurface.READWRITE,
        },
        streams=(),
    )

    with pytest.raises(ValueError, match="supports at most"):
        module.validate_execution_config(config)


def test_validate_execution_config_allows_large_runner_count_without_http_readwrite():
    module = load_module()

    config = module.ExecutionConfig(
        profile="custom",
        probes=("ftp",),
        schedule=module.SCHEDULE_CONCURRENT,
        runners=module.u64_http.SCREEN_RAM_RUNNER_SLOT_COUNT + 1,
        duration_s=60,
        probe_correctness={protocol: module.ProbeCorrectness.COMPLETE for protocol in module.DEFAULT_PROBES},
        uses_extended_flags=True,
        overrides=(),
        probe_surfaces={
            "ping": module.ProbeSurface.SMOKE,
            "http": module.ProbeSurface.READ,
            "ftp": module.ProbeSurface.READWRITE,
            "telnet": module.ProbeSurface.READWRITE,
        },
        streams=(),
    )

    module.validate_execution_config(config)


def test_help_output_mentions_restored_default_shape():
    module = load_module()

    help_text = module.build_parser().format_help()

    assert "Default: 12h soak with concurrent readwrite probes and audio+video streams." in help_text
    assert "ident targets UDP port 64 JSON discovery; raw64 targets the DMA-capable TCP port 64 command endpoint." in help_text
    assert "profile-default streams are disabled." in help_text
    assert "Correctness degradation: complete (finish and close cleanly), open (finish and skip orderly teardown), incomplete (abort before completion), invalid (send malformed or unsupported input)." in help_text
    assert "./u64_connection_test.py --profile stress --runners 4" in help_text
    assert "./u64_connection_test.py --profile soak --probes ping,http" in help_text


def test_resolve_execution_config_accepts_raw64_surface_override():
    module = load_module()
    parser = module.build_parser()

    resolved = module.resolve_execution_config(parser.parse_args(["--raw64-surface", "readwrite"]))

    assert resolved.probe_surfaces["raw64"] == module.ProbeSurface.READWRITE


def test_iteration_summary_appends_stream_health(capsys):
    module = load_module()
    settings = make_settings(module)
    state = module.ExecutionState(settings=settings, include_runner_context=False)

    class FakeStreamMonitor:
        def snapshots(self):
            return (
                module.u64_stream.StreamSnapshot(
                    kind=module.u64_stream.StreamKind.VIDEO,
                    status="OK",
                    packets_received=12,
                    lost_packets=0,
                    reordered_packets=0,
                    size_errors=0,
                    header_errors=0,
                    structure_errors=0,
                    timeout_errors=0,
                    first_packet_at=1.0,
                    last_packet_at=2.0,
                    last_sequence=11,
                    last_error="",
                ),
            )

    state.stream_monitor = FakeStreamMonitor()
    state.emit_iteration_summary(time.time(), 3, 1)

    output = capsys.readouterr().out
    assert "stream_video=OK,packets:12,lost:0,reordered:0,size_errs:0,header_errs:0,structure_errs:0,timeout_errs:0" in output


def test_operation_selection_avoids_fixed_log_cadence_aliasing():
    module = load_module()
    state = module.ExecutionState(settings=make_settings(module), include_runner_context=False, random_seed=7)

    indices = [state.next_probe_operation_index("telnet", 1, module.ProbeSurface.READWRITE, 5) for _ in range(30)]

    assert set(indices[:5]) == {0, 1, 2, 3, 4}
    assert set(indices[5:10]) == {0, 1, 2, 3, 4}
    assert len({indices[iteration - 1] for iteration in (10, 20, 30)}) == 3


def test_probe_iteration_sequence_randomizes_per_iteration_with_stable_seed():
    module = load_module()
    state = module.ExecutionState(settings=make_settings(module), include_runner_context=False, random_seed=11)

    first = [protocol for _index, protocol in state.probe_iteration_sequence(("ping", "http", "ftp", "telnet"), 1, 1)]
    second = [protocol for _index, protocol in state.probe_iteration_sequence(("ping", "http", "ftp", "telnet"), 1, 2)]

    assert sorted(first) == ["ftp", "http", "ping", "telnet"]
    assert sorted(second) == ["ftp", "http", "ping", "telnet"]
    assert first != second


def test_retryable_surface_error_includes_http_incomplete_read():
    module = load_runtime_module()

    assert module.is_retryable_surface_error(http.client.IncompleteRead(b"", 211))


def test_retryable_surface_error_includes_telnet_marker_miss():
    module = load_runtime_module()

    assert module.is_retryable_surface_error(RuntimeError("missing telnet text: Audio Mixer, Speaker Settings"))


def test_retryable_surface_error_includes_verification_mismatch():
    module = load_runtime_module()

    assert module.is_retryable_surface_error(RuntimeError("verification mismatch expected=+1 dB got=0 dB authoritative=0 dB latest_known=0 dB"))


def test_run_runner_iteration_sequential_uses_randomized_probe_order():
    module = load_module()
    calls = []
    settings = make_settings(module)
    config = make_config(module, probes=("ping", "http", "ftp", "telnet"))
    state = module.ExecutionState(settings=settings, include_runner_context=False, random_seed=11)

    def make_runner(name):
        def runner(current_settings, mode, *, context=None):
            del current_settings
            calls.append((name, mode, context.surface.value))
            return module.ProbeOutcome("OK", name, 1.0)

        return runner

    probe_runners = {protocol: make_runner(protocol) for protocol in ("ping", "http", "ftp", "telnet")}

    module.run_runner_iteration(1, 1, config, settings, state, sleep_fn=lambda value: None, probe_runners=probe_runners)

    assert sorted(calls) == [
        ("ftp", module.ProbeCorrectness.COMPLETE, "readwrite"),
        ("http", module.ProbeCorrectness.COMPLETE, "readwrite"),
        ("ping", module.ProbeCorrectness.COMPLETE, "smoke"),
        ("telnet", module.ProbeCorrectness.COMPLETE, "readwrite"),
    ]
    assert [protocol for protocol, _mode, _surface in calls] != ["ping", "http", "ftp", "telnet"]


def test_run_runner_iteration_concurrent_allows_overlap():
    module = load_module()
    barrier = threading.Barrier(2)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def make_runner(name):
        def runner(settings, mode, *, context=None):
            del settings, mode, context
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            barrier.wait(timeout=1)
            with lock:
                active -= 1
            return module.ProbeOutcome("OK", name, 1.0)

        return runner

    settings = make_settings(module)
    config = make_config(module, probes=("ping", "http"), schedule="concurrent")
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

    assert max_active == 2


def test_run_runner_iteration_concurrent_reports_in_default_protocol_order():
    module = load_module()
    settings = make_settings(module)
    config = make_config(module, probes=("ping", "http", "ftp", "telnet"), schedule="concurrent")
    state = module.ExecutionState(settings=settings, include_runner_context=False, random_seed=11)
    emitted = []

    state.emit_probe_outcome = lambda protocol, outcome, *, iteration, runner_id: emitted.append(protocol)

    def make_runner(name):
        def runner(current_settings, mode, *, context=None):
            del current_settings, mode, context
            return module.ProbeOutcome("OK", name, 1.0)

        return runner

    sequence = [protocol for _index, protocol in state.probe_iteration_sequence(config.probes, 1, 1)]

    results = module.run_runner_iteration(
        1,
        1,
        config,
        settings,
        state,
        sleep_fn=lambda value: None,
        probe_runners={protocol: make_runner(protocol) for protocol in ("ping", "http", "ftp", "telnet")},
    )

    assert sequence != ["ping", "http", "ftp", "telnet"]
    assert emitted == ["ping", "http", "ftp", "telnet"]
    assert [protocol for protocol, _outcome in results] == ["ping", "http", "ftp", "telnet"]


def test_run_runner_iteration_converts_unexpected_exceptions_to_failures():
    module = load_module()
    settings = make_settings(module)
    config = make_config(module, probes=("ping", "http"), schedule="concurrent")
    state = module.ExecutionState(settings=settings, include_runner_context=False, random_seed=5)

    results = module.run_runner_iteration(
        1,
        1,
        config,
        settings,
        state,
        sleep_fn=lambda value: None,
        probe_runners={
            "ping": lambda settings, mode, *, context=None: (_ for _ in ()).throw(RuntimeError("boom")),
            "http": lambda settings, mode, *, context=None: module.ProbeOutcome("OK", "http", 2.0),
            "ftp": lambda settings, mode, *, context=None: module.ProbeOutcome("OK", "ftp", 3.0),
            "telnet": lambda settings, mode, *, context=None: module.ProbeOutcome("OK", "telnet", 4.0),
        },
    )

    result_by_protocol = {protocol: outcome for protocol, outcome in results}
    assert set(result_by_protocol) == {"ping", "http"}
    assert result_by_protocol["ping"].result == "FAIL"
    assert result_by_protocol["ping"].detail == "ping failed: boom"


def test_multiple_runners_preserve_all_latency_samples():
    module = load_module()
    lock = threading.Lock()
    per_protocol_counts = {"ping": 0, "http": 0}
    base_latency_ms = {"ping": 1000.0, "http": 2000.0}

    def make_runner(protocol):
        def runner(settings, mode, *, context=None):
            del settings, mode, context
            with lock:
                per_protocol_counts[protocol] += 1
                call_index = per_protocol_counts[protocol]
            return module.ProbeOutcome("OK", f"{protocol} call={call_index}", base_latency_ms[protocol] + call_index)

        return runner

    settings = module.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 100, False)
    config = make_config(module, probes=("ping", "http"), schedule="concurrent", runners=3)
    state = module.ExecutionState(settings=settings, include_runner_context=False)
    stop_event = threading.Event()
    probe_runners = {
        "ping": make_runner("ping"),
        "http": make_runner("http"),
        "ftp": lambda settings, mode, *, context=None: module.ProbeOutcome("OK", "ftp", 3.0),
        "telnet": lambda settings, mode, *, context=None: module.ProbeOutcome("OK", "telnet", 4.0),
    }
    threads = [
        threading.Thread(
            target=module.run_runner_loop,
            args=(runner_id, config, settings, state, stop_event),
            kwargs={"sleep_fn": lambda value: None, "probe_runners": probe_runners, "max_iterations": 2},
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


def test_run_extended_primes_temp_dir_for_ftp_read_surface(monkeypatch):
    module = load_module()
    settings = make_settings(module)
    config = module.ExecutionConfig(
        profile="soak",
        probes=("ftp",),
        schedule="sequential",
        runners=1,
        duration_s=1,
        probe_correctness={protocol: module.ProbeCorrectness.COMPLETE for protocol in module.DEFAULT_PROBES},
        uses_extended_flags=True,
        overrides=(),
        probe_surfaces={"ftp": module.ProbeSurface.READ},
    )
    calls = []

    monkeypatch.setattr(module.u64_ftp, "try_prime_temp_dir", lambda current_settings, **kwargs: calls.append(current_settings.host) or ())
    monkeypatch.setattr(module, "run_runner_loop", lambda *args, **kwargs: 0)

    assert module.run_extended(config, settings) == 0
    assert calls == ["host"]


def test_run_extended_returns_failure_when_stream_monitor_reports_failure(monkeypatch):
    module = load_module()
    settings = make_settings(module)
    config = module.ExecutionConfig(
        profile="soak",
        probes=("ping",),
        schedule="sequential",
        runners=1,
        duration_s=1,
        probe_correctness={protocol: module.ProbeCorrectness.COMPLETE for protocol in module.DEFAULT_PROBES},
        uses_extended_flags=True,
        overrides=("stream",),
        probe_surfaces={"ping": module.ProbeSurface.SMOKE},
        streams=("video",),
    )

    monkeypatch.setattr(module, "run_runner_loop", lambda *args, **kwargs: 0)

    class FakeStreamMonitor:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

        def stop(self):
            return None

        def snapshots(self):
            return (
                module.u64_stream.StreamSnapshot(
                    kind=module.u64_stream.StreamKind.VIDEO,
                    status="FAIL",
                    packets_received=10,
                    lost_packets=1,
                    reordered_packets=0,
                    size_errors=0,
                    header_errors=0,
                    structure_errors=0,
                    timeout_errors=0,
                    first_packet_at=1.0,
                    last_packet_at=2.0,
                    last_sequence=9,
                    last_error="lost packet",
                ),
            )

    monkeypatch.setattr(module.u64_stream, "StreamMonitor", FakeStreamMonitor)

    assert module.run_extended(config, settings) == 1


def test_historical_correctness_mapping_is_pinned_to_git_evidence():
    module = load_module()

    assert module.HISTORICAL_CORRECTNESS_EVIDENCE["ftp"]["open"]["commit"] == "f0ef5db"
    assert module.HISTORICAL_CORRECTNESS_EVIDENCE["ftp"]["incomplete"]["commit"] == "37314b1"
    assert module.HISTORICAL_CORRECTNESS_EVIDENCE["telnet"]["open"]["commit"] == "f0ef5db"
    assert module.HISTORICAL_CORRECTNESS_EVIDENCE["telnet"]["incomplete"]["commit"] == "37314b1"
