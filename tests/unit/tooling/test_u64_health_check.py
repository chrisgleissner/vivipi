from __future__ import annotations

from pathlib import Path

from vivipi.core.execution import CheckExecutionResult
from vivipi.core.models import CheckObservation, CheckType, Status
from vivipi.core.text import overview_row_layout
from tests.unit.tooling._script_loader import load_script_module


def load_module():
    return load_script_module("u64_health_check")


def make_configs(tmp_path: Path) -> tuple[Path, Path]:
    build_config = tmp_path / "build-deploy.yaml"
    build_config.write_text(
        """
wifi:
  host_aliases:
    c64: 192.0.2.10
    u64: 192.0.2.20
checks_config: checks.yaml
""".strip(),
        encoding="utf-8",
    )
    checks_config = tmp_path / "checks.yaml"
    checks_config.write_text(
        """
checks:
  - name: C64U REST
    type: rest
    target: http://c64/v1/version
    password: ${VIVIPI_NETWORK_PASSWORD}
    interval_s: 10
    timeout_s: 8
  - name: C64U FTP
    type: ftp
    target: c64
    username: ${VIVIPI_NETWORK_USERNAME}
    password: ${VIVIPI_NETWORK_PASSWORD}
    interval_s: 10
    timeout_s: 8
  - name: C64U TELNET
    type: telnet
    target: c64:23
    username: ${VIVIPI_NETWORK_USERNAME}
    password: ${VIVIPI_NETWORK_PASSWORD}
    interval_s: 10
    timeout_s: 8
  - name: U64 REST
    type: rest
    target: http://u64/v1/version
    password: ${VIVIPI_NETWORK_PASSWORD}
    interval_s: 10
    timeout_s: 8
  - name: U64 FTP
    type: ftp
    target: u64
    username: ${VIVIPI_NETWORK_USERNAME}
    password: ${VIVIPI_NETWORK_PASSWORD}
    interval_s: 10
    timeout_s: 8
  - name: U64 TELNET
    type: telnet
    target: u64:23
    username: ${VIVIPI_NETWORK_USERNAME}
    password: ${VIVIPI_NETWORK_PASSWORD}
    interval_s: 10
    timeout_s: 8
""".strip(),
        encoding="utf-8",
    )
    return build_config, checks_config


def make_result(definition, status: Status, latency_ms: float | None, details: str) -> CheckExecutionResult:
    return CheckExecutionResult(
        source_identifier=definition.identifier,
        observations=(
            CheckObservation(
                identifier=definition.identifier,
                name=definition.name,
                status=status,
                details=details,
                latency_ms=latency_ms,
                observed_at_s=0.0,
            ),
        ),
        probe_latency_ms=latency_ms,
    )


def test_run_target_orders_checks_formats_like_overview_and_resolves_aliases(tmp_path, monkeypatch, capsys):
    module = load_module()
    build_config, _checks_config = make_configs(tmp_path)
    seen = []
    results = {
        CheckType.PING: (Status.OK, 7.2, "reachable"),
        CheckType.HTTP: (Status.OK, 15.4, "HTTP 200"),
        CheckType.FTP: (Status.FAIL, 20.2, "timed out"),
        CheckType.TELNET: (Status.OK, 31.0, "session ready"),
    }

    def fake_build_executor():
        def executor(definition, _now_s):
            seen.append((definition.name, definition.target, definition.check_type))
            status, latency_ms, details = results[definition.check_type]
            return make_result(definition, status, latency_ms, details)

        return executor

    monkeypatch.setattr(module, "build_executor", fake_build_executor)

    exit_code = module.run_target(
        "c64u",
        build_config_path=build_config,
        env={"VIVIPI_NETWORK_USERNAME": "user", "VIVIPI_NETWORK_PASSWORD": "secret"},
    )

    assert exit_code == 1
    assert seen == [
        ("C64U PING", "192.0.2.10", CheckType.PING),
        ("C64U REST", "http://192.0.2.10/v1/version", CheckType.HTTP),
        ("C64U FTP", "192.0.2.10", CheckType.FTP),
        ("C64U TELNET", "192.0.2.10:23", CheckType.TELNET),
    ]
    assert capsys.readouterr().out.splitlines() == [
        overview_row_layout("C64U PING", "OK").text + " (7ms)",
        overview_row_layout("C64U REST", "OK").text + " (15ms)",
        overview_row_layout("C64U FTP", "FAIL").text + " (20ms) timed out",
        overview_row_layout("C64U TELNET", "OK").text + " (31ms)",
    ]


def test_main_returns_error_when_required_target_checks_are_missing(tmp_path, capsys):
    module = load_module()
    build_config = tmp_path / "build-deploy.yaml"
    build_config.write_text(
        """
wifi:
  host_aliases:
    u64: 192.0.2.20
checks_config: checks.yaml
""".strip(),
        encoding="utf-8",
    )
    checks_config = tmp_path / "checks.yaml"
    checks_config.write_text(
        """
checks:
  - name: U64 REST
    type: rest
    target: http://u64/v1/version
    interval_s: 10
    timeout_s: 8
""".strip(),
        encoding="utf-8",
    )

    exit_code = module.main(["u64", "--build-config", str(build_config)])

    assert exit_code == 2
    assert "missing U64 checks: FTP, TELNET" in capsys.readouterr().err
