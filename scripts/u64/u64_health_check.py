#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]  # scripts/u64/<this> -> repo root
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vivipi.core.config import load_checks_config, slugify  # noqa: E402
from vivipi.core.models import CheckDefinition, CheckType  # noqa: E402
from vivipi.core.text import overview_row_layout  # noqa: E402
from vivipi.runtime.checks import build_executor, build_runtime_definitions  # noqa: E402


TARGET_LABELS = {
    "c64u": "C64U",
    "u64": "U64",
    "u2": "U2",
}
PROBE_ORDER = {
    CheckType.PING: 0,
    CheckType.HTTP: 1,
    CheckType.IDENT: 2,
    CheckType.DMA: 3,
    CheckType.FTP: 4,
    CheckType.TELNET: 5,
}
REQUIRED_CHECK_TYPES = (CheckType.HTTP, CheckType.FTP, CheckType.TELNET)
HEALTH_CHECK_TIMEOUT_CAP_S = 2


def preferred_build_config_path(repo_root: Path = REPO_ROOT) -> Path:
    local_path = repo_root / "config" / "build-deploy.local.yaml"
    if local_path.exists():
        return local_path
    return repo_root / "config" / "build-deploy.yaml"


def load_build_settings(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle.read()) or {}
    if not isinstance(raw, dict):
        raise ValueError("build config must be a mapping")
    return raw


def resolve_checks_path(build_config_path: Path, settings: dict[str, object], checks_path: str | None) -> Path:
    if checks_path is not None:
        return Path(checks_path).resolve()
    configured = settings.get("checks_config")
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError("checks_config must be present in the build config")
    return (build_config_path.parent / configured).resolve()


def _host_aliases(settings: dict[str, object]) -> dict[str, str]:
    wifi = settings.get("wifi")
    if not isinstance(wifi, dict):
        return {}
    aliases = wifi.get("host_aliases")
    if not isinstance(aliases, dict):
        return {}
    resolved: dict[str, str] = {}
    for key, value in aliases.items():
        alias = str(value).strip()
        if alias:
            resolved[str(key).strip()] = alias
    return resolved


def _target_host(target: str) -> str:
    raw_target = str(target).strip()
    if not raw_target:
        raise ValueError("target must be non-empty")
    if "://" in raw_target:
        parsed = urlparse(raw_target)
        if not parsed.hostname:
            raise ValueError("target must include a host")
        return parsed.hostname
    host, separator, port = raw_target.rpartition(":")
    if separator and host and port.isdigit():
        return host
    return raw_target


def _ping_definition(label: str, host: str, timeout_s: int, interval_s: int) -> CheckDefinition:
    return CheckDefinition(
        identifier=f"{slugify(label)}-ping",
        name=f"{label} PING",
        check_type=CheckType.PING,
        target=host,
        interval_s=interval_s,
        timeout_s=timeout_s,
    )


def _direct_listener_definition(
    label: str,
    host: str,
    check_type: CheckType,
    timeout_s: int,
    interval_s: int,
    *,
    password: str | None = None,
) -> CheckDefinition:
    return CheckDefinition(
        identifier=f"{slugify(label)}-{check_type.value.lower()}",
        name=f"{label} {check_type.value}",
        check_type=check_type,
        target=host,
        interval_s=interval_s,
        timeout_s=timeout_s,
        password=password,
    )


def _runtime_item(definition: CheckDefinition) -> dict[str, object]:
    return {
        "id": definition.identifier,
        "name": definition.name,
        "type": definition.check_type.value,
        "target": definition.target,
        "interval_s": definition.interval_s,
        "timeout_s": definition.timeout_s,
        "method": definition.method,
        "username": definition.username,
        "password": definition.password,
        "service_prefix": definition.service_prefix,
    }


def _health_check_definition(definition: CheckDefinition) -> CheckDefinition:
    return replace(
        definition,
        timeout_s=min(definition.timeout_s, HEALTH_CHECK_TIMEOUT_CAP_S),
    )


def load_target_definitions(
    target_name: str,
    *,
    build_config_path: Path | None = None,
    checks_path: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[CheckDefinition, ...]:
    label = TARGET_LABELS[target_name]
    config_path = (build_config_path or preferred_build_config_path()).resolve()
    settings = load_build_settings(config_path)
    definitions = load_checks_config(resolve_checks_path(config_path, settings, checks_path), env=env)
    prefix = f"{label} "
    checks_by_type: dict[CheckType, CheckDefinition] = {}
    for definition in definitions:
        if not definition.name.startswith(prefix) or definition.check_type not in REQUIRED_CHECK_TYPES:
            continue
        if definition.check_type in checks_by_type:
            raise ValueError(f"duplicate {definition.check_type.value} check for {label}")
        checks_by_type[definition.check_type] = definition
    missing = [check_type.value for check_type in REQUIRED_CHECK_TYPES if check_type not in checks_by_type]
    if missing:
        raise ValueError(f"missing {label} checks: {', '.join(missing)}")

    reference = _health_check_definition(checks_by_type[CheckType.HTTP])
    host = _target_host(reference.target)
    selected = [
        _ping_definition(
            label,
            host,
            reference.timeout_s,
            reference.interval_s,
        ),
        reference,
        _direct_listener_definition(
            label,
            host,
            CheckType.IDENT,
            reference.timeout_s,
            reference.interval_s,
        ),
        _direct_listener_definition(
            label,
            host,
            CheckType.DMA,
            reference.timeout_s,
            reference.interval_s,
            password=reference.password,
        ),
        _health_check_definition(checks_by_type[CheckType.FTP]),
        _health_check_definition(checks_by_type[CheckType.TELNET]),
    ]
    runtime_config = {
        "wifi": {"host_aliases": _host_aliases(settings)},
        "checks": [_runtime_item(definition) for definition in selected],
    }
    resolved = build_runtime_definitions(runtime_config)
    return tuple(sorted(resolved, key=lambda definition: PROBE_ORDER[definition.check_type]))


def _primary_observation(definition: CheckDefinition, result):
    for observation in result.observations:
        if observation.identifier == definition.identifier or observation.source_identifier == definition.identifier:
            return observation
    if len(result.observations) == 1:
        return result.observations[0]
    return None


def _one_line(text: str) -> str:
    return " ".join(str(text).split()).strip()


def format_result_line(definition: CheckDefinition, result) -> tuple[str, str]:
    observation = _primary_observation(definition, result)
    status = "?"
    detail = ""
    latency_ms = getattr(result, "probe_latency_ms", None)
    if observation is not None:
        status = str(getattr(observation.status, "value", observation.status)).strip() or "?"
        detail = _one_line(observation.details)
        if observation.latency_ms is not None:
            latency_ms = observation.latency_ms
    line = overview_row_layout(definition.name, status).text
    if latency_ms is not None:
        line += f" ({int(round(latency_ms))}ms)"
    if status != "OK" and detail:
        line += f" {detail}"
    return line, status


def run_target(
    target_name: str,
    *,
    build_config_path: Path | None = None,
    checks_path: str | None = None,
    env: dict[str, str] | None = None,
) -> int:
    executor = build_executor()
    exit_code = 0
    for definition in load_target_definitions(
        target_name,
        build_config_path=build_config_path,
        checks_path=checks_path,
        env=env,
    ):
        result = executor(definition, time.time())
        line, status = format_result_line(definition, result)
        print(line)
        if status != "OK":
            exit_code = 1
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run concise ViviPi-compatible health checks for C64U, U64, or U2"
    )
    parser.add_argument("target", choices=tuple(TARGET_LABELS))
    parser.add_argument("--build-config", type=Path, default=None)
    parser.add_argument("--checks-config", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run_target(
            args.target,
            build_config_path=args.build_config,
            checks_path=args.checks_config,
            env=dict(os.environ),
        )
    except Exception as error:
        print(_one_line(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
