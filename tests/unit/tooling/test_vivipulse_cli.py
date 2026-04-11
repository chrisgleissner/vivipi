from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vivipi.core.execution import CheckExecutionResult
from vivipi.core.models import CheckDefinition, CheckObservation, CheckType, Status
from vivipi.core.probe_trace import ProbeTraceCollector, ProbeTraceJsonlWriter
from vivipi.tooling import vivipulse as tooling_vivipulse


def make_definition(identifier: str, *, target: str = "device.local", check_type: CheckType = CheckType.PING):
    return CheckDefinition(
        identifier=identifier,
        name=identifier.upper(),
        check_type=check_type,
        target=target,
        interval_s=15,
        timeout_s=10,
    )


def success_result(definition: CheckDefinition, observed_at_s: float, detail: str = "reachable") -> CheckExecutionResult:
    return CheckExecutionResult(
        source_identifier=definition.identifier,
        observations=(
            CheckObservation(
                identifier=definition.identifier,
                name=definition.name,
                status=Status.OK,
                details=detail,
                latency_ms=10.0,
                observed_at_s=observed_at_s,
            ),
        ),
    )


def failure_result(definition: CheckDefinition, observed_at_s: float, detail: str = "timeout") -> CheckExecutionResult:
    return CheckExecutionResult(
        source_identifier=definition.identifier,
        observations=(
            CheckObservation(
                identifier=definition.identifier,
                name=definition.name,
                status=Status.FAIL,
                details=detail,
                latency_ms=200.0,
                observed_at_s=observed_at_s,
            ),
        ),
    )


def make_args(**overrides):
    defaults = {
        "checks_config": None,
        "runtime_config": None,
        "build_config": None,
        "mode": "plan",
        "duration": None,
        "passes": None,
        "same_host_backoff_ms": None,
        "allow_concurrent_same_host": False,
        "target": None,
        "check_id": None,
        "artifacts_dir": None,
        "stop_on_failure": False,
        "interactive_recovery": False,
        "resume_after_recovery": False,
        "max_experiments": 4,
        "ultimate_repo": None,
        "debug": False,
        "json": False,
        "parity_mode": False,
        "firmware_trace": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_parse_duration_supports_suffixes():
    assert tooling_vivipulse.parse_duration("90") == 90.0
    assert tooling_vivipulse.parse_duration("2h") == 7200.0
    assert tooling_vivipulse.parse_duration("30m") == 1800.0
    assert tooling_vivipulse.parse_duration("15s") == 15.0


def test_parse_duration_rejects_blank():
    with pytest.raises(ValueError, match="must not be blank"):
        tooling_vivipulse.parse_duration("   ")


def test_resolve_input_reuses_build_runtime_definitions_for_checks_config(tmp_path, monkeypatch):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text("checks: []\n", encoding="utf-8")
    expected = (make_definition("alpha"),)
    captured = {}

    monkeypatch.setattr(tooling_vivipulse, "load_checks_config", lambda path: expected)

    def fake_build_runtime_definitions(runtime_config):
        captured["runtime_config"] = runtime_config
        return expected

    monkeypatch.setattr(tooling_vivipulse, "build_runtime_definitions", fake_build_runtime_definitions)

    resolved = tooling_vivipulse.resolve_input(make_args(checks_config=str(config_path)))

    assert resolved.definitions == expected
    assert captured["runtime_config"]["checks"][0]["id"] == "alpha"
    assert "vivipi.runtime.checks.build_runtime_definitions" in resolved.parser_reuse


def test_resolve_input_runtime_config_rejects_non_mapping_root(tmp_path):
    runtime_path = tmp_path / "config.json"
    runtime_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        tooling_vivipulse.resolve_input(make_args(runtime_config=str(runtime_path)))


def test_resolve_input_rejects_multiple_sources():
    with pytest.raises(ValueError, match="choose exactly one"):
        tooling_vivipulse.resolve_input(make_args(checks_config="a", runtime_config="b"))


def test_resolve_input_rejects_empty_filtered_selection(tmp_path, monkeypatch):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text("checks: []\n", encoding="utf-8")
    monkeypatch.setattr(tooling_vivipulse, "load_checks_config", lambda path: (make_definition("alpha"),))
    monkeypatch.setattr(
        tooling_vivipulse,
        "build_runtime_definitions",
        lambda runtime_config: (make_definition("alpha"),),
    )

    with pytest.raises(ValueError, match="no checks matched"):
        tooling_vivipulse.resolve_input(make_args(checks_config=str(config_path), check_id=["missing"]))


def test_resolve_input_reuses_build_config_stack(tmp_path, monkeypatch):
    build_config = tmp_path / "build-deploy.yaml"
    build_config.write_text("project: {}\n", encoding="utf-8")
    expected = (make_definition("alpha"),)
    captured = []

    monkeypatch.setattr(tooling_vivipulse, "load_build_deploy_settings", lambda path: {"checks_config": "checks.yaml"})
    monkeypatch.setattr(tooling_vivipulse, "_resolve_checks_path", lambda path, settings: path.parent / settings["checks_config"])
    monkeypatch.setattr(tooling_vivipulse, "load_runtime_checks", lambda path: expected)

    def fake_render_device_runtime_config(settings, checks):
        captured.append(("render", settings, checks))
        return {"checks": [{"id": "alpha", "name": "ALPHA", "type": "PING", "target": "device.local"}]}

    monkeypatch.setattr(tooling_vivipulse, "render_device_runtime_config", fake_render_device_runtime_config)
    monkeypatch.setattr(tooling_vivipulse, "build_runtime_definitions", lambda runtime_config: expected)

    resolved = tooling_vivipulse.resolve_input(make_args(build_config=str(build_config)))

    assert resolved.input_kind == "build-config"
    assert captured[0][0] == "render"
    assert "vivipi.tooling.build_deploy.render_device_runtime_config" in resolved.parser_reuse


def test_resolve_input_applies_cli_probe_schedule_overrides(tmp_path):
    runtime_path = tmp_path / "config.json"
    runtime_path.write_text(
        json.dumps(
            {
                "checks": [{"id": "alpha", "name": "Alpha", "type": "PING", "target": "device.local"}],
                "probe_schedule": {"allow_concurrent_same_host": False, "same_host_backoff_ms": 250},
            }
        ),
        encoding="utf-8",
    )

    resolved = tooling_vivipulse.resolve_input(
        make_args(
            runtime_config=str(runtime_path),
            same_host_backoff_ms=900,
            allow_concurrent_same_host=True,
        )
    )

    assert resolved.profile.same_host_backoff_ms == 900
    assert resolved.profile.allow_concurrent_same_host is True


def test_inspect_ultimate_repo_and_summary_render(tmp_path):
    repo = tmp_path / "1541ultimate"
    for relative_path in (
        "software/network/ftpd.cc",
        "software/network/socket_gui.cc",
        "software/network/httpd.cc",
        "software/httpd/c-version/lib/server.c",
        "software/httpd/c-version/lib/server.h",
        "software/network/config/lwipopts.h",
        "software/network/network_config.cc",
        "target/u64/nios2/ultimate/Makefile",
    ):
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content\n", encoding="utf-8")

    report = tooling_vivipulse.inspect_ultimate_repo(repo)
    summary = tooling_vivipulse.render_firmware_research_summary(report)

    assert report.hints.recommended_same_host_backoff_ms == 1000
    assert report.hints.recommended_allow_concurrent_same_host is False
    assert "Confirmed Facts:" in summary
    assert "Strong Inferences:" in summary
    assert "MEMP_NUM_NETCONN = 16" in summary
    assert "suspended FreeRTOS task" in summary


def test_inspect_ultimate_repo_rejects_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="Ultimate repository not found"):
        tooling_vivipulse.inspect_ultimate_repo(tmp_path / "missing")


def test_main_plan_mode_writes_artifacts_and_json(tmp_path):
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text(
        """
checks:
  - name: Alpha
    type: ping
    target: device.local
    interval_s: 15
    timeout_s: 10
""".strip(),
        encoding="utf-8",
    )
    output = io.StringIO()

    exit_code = tooling_vivipulse.main(
        [
            "--checks-config",
            str(checks_path),
            "--mode",
            "plan",
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--json",
        ],
        output_stream=output,
    )

    payload = json.loads(output.getvalue())
    artifact_dir = Path(payload["artifacts_dir"])

    assert exit_code == 0
    assert payload["mode"] == "plan"
    assert artifact_dir.is_dir()
    assert (artifact_dir / "trace.jsonl").read_text(encoding="utf-8") == ""
    assert (artifact_dir / "transport-trace.jsonl").read_text(encoding="utf-8") == ""
    assert "firmware.main.main -> firmware.runtime.run_forever" in (artifact_dir / "reuse-map.txt").read_text(encoding="utf-8")


def test_main_local_mode_runs_one_pass_without_extra_flags(tmp_path, monkeypatch):
    runtime_path = tmp_path / "config.json"
    runtime_path.write_text(
        json.dumps(
            {
                "checks": [
                    {"id": "alpha", "name": "Alpha", "type": "PING", "target": "shared.local"},
                ],
                "probe_schedule": {"allow_concurrent_same_host": False, "same_host_backoff_ms": 250},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        tooling_vivipulse,
        "build_executor",
        lambda: (lambda definition, observed_at_s: success_result(definition, observed_at_s)),
    )

    output = io.StringIO()

    exit_code = tooling_vivipulse.main(
        [
            "--runtime-config",
            str(runtime_path),
            "--mode",
            "local",
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--json",
        ],
        output_stream=output,
    )

    payload = json.loads(output.getvalue())
    artifact_dir = Path(payload["artifacts_dir"])

    assert exit_code == 0
    assert payload["mode"] == "local"
    assert payload["outcome"]["request_count"] == 1
    assert artifact_dir.is_dir()


def test_parity_profile_resets_search_knobs():
    profile = tooling_vivipulse._parity_profile(
        tooling_vivipulse.VivipulseProfile(
            allow_concurrent_hosts=False,
            allow_concurrent_same_host=False,
            same_host_backoff_ms=1000,
            pass_spacing_s=1.0,
            same_host_spacing_ms=250,
            check_order="network-heavy-first",
            interval_scale_by_check_id={"alpha": 2.0},
            disabled_check_ids=("beta",),
        )
    )

    assert profile.same_host_backoff_ms == 1000
    assert profile.pass_spacing_s == 0.0
    assert profile.same_host_spacing_ms == 0
    assert profile.check_order == "network-light-first"
    assert profile.interval_scale_by_check_id == {}
    assert profile.disabled_check_ids == ()


def test_main_local_mode_writes_parity_summary_when_firmware_trace_is_provided(tmp_path, monkeypatch):
    runtime_path = tmp_path / "config.json"
    runtime_path.write_text(
        json.dumps(
            {
                "checks": [
                    {"id": "alpha", "name": "Alpha", "type": "PING", "target": "shared.local"},
                ],
                "probe_schedule": {"allow_concurrent_same_host": False, "same_host_backoff_ms": 250},
            }
        ),
        encoding="utf-8",
    )

    firmware_trace_path = tmp_path / "firmware-trace.jsonl"
    firmware_writer = ProbeTraceJsonlWriter(firmware_trace_path)
    firmware_collector = ProbeTraceCollector(firmware_writer.write, source="firmware", mode="runtime")
    definition = make_definition("alpha")
    firmware_collector.emit(definition, "probe-start", {"timeout_s": 10})
    firmware_collector.emit(definition, "probe-end", {"status": "OK", "detail": "reachable", "latency_ms": 10.0})
    firmware_writer.close()

    def fake_build_executor(trace_sink=None):
        def executor(definition: CheckDefinition, observed_at_s: float):
            if trace_sink is not None:
                trace_sink(definition, "probe-start", {"timeout_s": definition.timeout_s})
            result = success_result(definition, observed_at_s)
            if trace_sink is not None:
                trace_sink(definition, "probe-end", {"status": "OK", "detail": "reachable", "latency_ms": 10.0})
            return result

        return executor

    monkeypatch.setattr(tooling_vivipulse, "build_executor", fake_build_executor)
    output = io.StringIO()

    exit_code = tooling_vivipulse.main(
        [
            "--runtime-config",
            str(runtime_path),
            "--mode",
            "local",
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--parity-mode",
            "--firmware-trace",
            str(firmware_trace_path),
            "--json",
        ],
        output_stream=output,
    )

    payload = json.loads(output.getvalue())
    artifact_dir = Path(payload["artifacts_dir"])

    assert exit_code == 0
    assert "Ordering match: True" in (artifact_dir / "parity-summary.txt").read_text(encoding="utf-8")


def test_render_helpers_cover_empty_branches(tmp_path):
    resolved = tooling_vivipulse.ResolvedInput(
        definitions=(make_definition("alpha"),),
        profile=tooling_vivipulse.VivipulseProfile(),
        runtime_config={},
        input_kind="runtime-config",
        input_path=tmp_path / "config.json",
        parser_reuse=("json.load",),
    )
    plan = tooling_vivipulse.build_plan_view(resolved.definitions, resolved.profile)
    payload = tooling_vivipulse._summary_payload(
        mode="plan",
        artifacts_dir=tmp_path,
        resolved=resolved,
        plan=plan,
    )

    assert "plan" in payload
    assert tooling_vivipulse.render_failure_boundary_summary(
        tooling_vivipulse.RunOutcome(
            mode="plan",
            profile=resolved.profile,
            started_at="start",
            completed_at="end",
            trace_events=(),
            failure_boundaries=(),
            selected_definition_ids=("alpha",),
            blocked_host_keys=(),
        )
    ) == "No transport failure boundaries were recorded.\n"
    assert tooling_vivipulse.render_search_summary(None) == "Search mode was not run for this invocation.\n"
    assert tooling_vivipulse.render_soak_summary(None, None) == "Soak mode was not run for this invocation.\n"


def test_main_reproduce_mode_wires_interactive_recovery(tmp_path, monkeypatch):
    runtime_path = tmp_path / "config.json"
    runtime_path.write_text(
        json.dumps(
            {
                "checks": [
                    {"id": "alpha", "name": "Alpha", "type": "PING", "target": "shared.local"},
                    {"id": "beta", "name": "Beta", "type": "HTTP", "target": "http://shared.local/health"},
                    {"id": "gamma", "name": "Gamma", "type": "FTP", "target": "ftp://shared.local"},
                ],
                "probe_schedule": {"allow_concurrent_same_host": False, "same_host_backoff_ms": 0},
            }
        ),
        encoding="utf-8",
    )

    failures = {"beta": 1}

    def fake_build_executor():
        def executor(definition: CheckDefinition, observed_at_s: float):
            if failures.get(definition.identifier):
                failures[definition.identifier] -= 1
                return failure_result(definition, observed_at_s)
            return success_result(definition, observed_at_s)

        return executor

    monkeypatch.setattr(tooling_vivipulse, "build_executor", fake_build_executor)
    output = io.StringIO()
    prompts = []

    exit_code = tooling_vivipulse.main(
        [
            "--runtime-config",
            str(runtime_path),
            "--mode",
            "reproduce",
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--interactive-recovery",
            "--resume-after-recovery",
        ],
        prompt=lambda text: prompts.append(text) or "resume",
        output_stream=output,
    )

    artifact_dir = next((tmp_path / "artifacts").iterdir())
    trace_lines = (artifact_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()

    assert exit_code == 0
    assert prompts == ["Type 'resume' once recovery is complete: "]
    assert len(trace_lines) == 3
    assert "Minimum recovery action" in output.getvalue()


def test_recovery_callback_factory_covers_dns_and_non_resume_paths():
    output = io.StringIO()
    callback = tooling_vivipulse._recovery_callback_factory(
        make_args(resume_after_recovery=False),
        prompt=lambda text: "ignored",
        output_stream=output,
    )
    boundary = SimpleNamespace(
        target="host.local",
        last_success=None,
        first_failure=SimpleNamespace(
            sequence=2,
            check_id="alpha",
            response_summary="dns: name or service not known",
            failure_class="dns",
        ),
    )

    assert callback(boundary) is False
    assert "restore name resolution" in output.getvalue()


def test_main_soak_requires_duration(tmp_path):
    runtime_path = tmp_path / "config.json"
    runtime_path.write_text(
        json.dumps({"checks": [{"id": "alpha", "name": "Alpha", "type": "PING", "target": "device.local"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires --duration"):
        tooling_vivipulse.main(
            ["--runtime-config", str(runtime_path), "--mode", "soak", "--artifacts-dir", str(tmp_path / "artifacts")],
            output_stream=io.StringIO(),
        )


def test_main_search_requires_repo_when_default_is_missing(tmp_path, monkeypatch):
    runtime_path = tmp_path / "config.json"
    runtime_path.write_text(
        json.dumps({"checks": [{"id": "alpha", "name": "Alpha", "type": "PING", "target": "device.local"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tooling_vivipulse, "repository_root", lambda: tmp_path / "repo-root")

    with pytest.raises(FileNotFoundError, match="requires the Ultimate firmware checkout"):
        tooling_vivipulse.main(
            ["--runtime-config", str(runtime_path), "--mode", "search", "--artifacts-dir", str(tmp_path / "artifacts")],
            output_stream=io.StringIO(),
        )
